import { describe, expect, it } from "vitest";
import type { Measure, Widget } from "../api/types";
import { axisTime, formatValue, shortLabel } from "./format";
import { binCounts, BUILDERS, chartOption, compactOption, statusFor, tableModel, type ChartInput } from "./options";
import { theme } from "./theme";

const measures: Measure[] = [
  { id: "pass_rate", expr: "ratio(count_if(status == 'passed'), count())", format: "percent", label: "Pass rate" },
  { id: "failed", expr: "count_if(status == 'failed')", format: "number" },
  { id: "p95", expr: "percentile(latency_ms, 95)", format: "duration_ms" },
  ...["min", "p25", "p50", "p75", "max"].map((s) => ({ id: `d_${s}`, expr: "min(duration_ms)" })),
];

function w(type: Widget["type"], query: Partial<Widget["query"]>, extra: Partial<Widget> = {}): Widget {
  return { id: `w_${type}`, type, title: `${type} chart`, query: { dataset: "ds", ...query }, ...extra } as Widget;
}

const input = (widget: Widget, columns: string[], rows: unknown[][]): ChartInput => ({ widget, result: { columns, rows }, measures });

describe("chart mappings", () => {
  it("kpi shows the formatted value, coloured by threshold, with the target", () => {
    const opt = BUILDERS.kpi(input(w("kpi", { measures: ["pass_rate"] }, { target: 0.95, thresholds: [{ below: 0.9, color: "critical" }] }), ["pass_rate"], [[0.82]]));
    const texts = (opt.graphic as Array<{ style: { text: string; fill: string } }>).map((g) => g.style);
    expect(texts[0].text).toBe("82%");
    expect(texts[0].fill).toBe(theme.status.critical);
    expect(texts[1].text).toBe("target 95%");
  });

  it("line has one series per measure, a category x axis and threshold/annotation mark lines", () => {
    const widget = w("line", { dimensions: ["build"], measures: ["pass_rate"] }, {
      thresholds: [{ below: 0.9, color: "warning" }], annotations: [{ type: "rule", y: 0.95, label: "Target" }],
    });
    const opt = BUILDERS.line(input(widget, ["build", "pass_rate"], [["101", 0.8], ["102", 0.9]]));
    expect((opt.xAxis as { data: string[] }).data).toEqual(["101", "102"]);
    const series = opt.series as Array<{ type: string; data: number[]; markLine: { data: unknown[] } }>;
    expect(series).toHaveLength(1);
    expect(series[0].type).toBe("line");
    expect(series[0].data).toEqual([0.8, 0.9]);
    expect(series[0].markLine.data).toHaveLength(2);
  });

  it("line splits series by the second dimension", () => {
    const opt = BUILDERS.line(input(w("line", { dimensions: ["build", "suite"], measures: ["failed"] }), ["build", "suite", "failed"],
      [["101", "api", 1], ["101", "ui", 2], ["102", "api", 3]]));
    const series = opt.series as Array<{ name: string; data: Array<number | null> }>;
    expect(series.map((s) => s.name)).toEqual(["api", "ui"]);
    expect(series[1].data).toEqual([2, null]);
  });

  it("area fills under the line", () => {
    const opt = BUILDERS.area(input(w("area", { dimensions: ["day"], measures: ["failed"] }), ["day", "failed"], [["a", 1], ["b", 2]]));
    expect((opt.series as Array<{ areaStyle: unknown }>)[0].areaStyle).toBeTruthy();
  });

  it("bar can be horizontal and log-scaled", () => {
    const opt = BUILDERS.bar(input(w("bar", { dimensions: ["case"], measures: ["p95"] }, { options: { horizontal: true, log_scale: true } }),
      ["case", "p95"], [["a very long test case name that keeps going", 1200]]));
    expect((opt.yAxis as { type: string }).type).toBe("category");
    expect((opt.xAxis as { type: string }).type).toBe("log");
    const label = (opt.yAxis as { axisLabel: { formatter: (v: string) => string } }).axisLabel.formatter("a very long test case name that keeps going");
    expect(label.length).toBeLessThanOrEqual(18);
  });

  it("stacked bar stacks, and percent stacking normalises each category to 1", () => {
    const rows = [["101", "passed", 3], ["101", "failed", 1], ["102", "passed", 1], ["102", "failed", 1]];
    const stacked = BUILDERS.stacked_bar(input(w("stacked_bar", { dimensions: ["build", "status"], measures: ["failed"] }), ["build", "status", "failed"], rows));
    expect((stacked.series as Array<{ stack: string }>).every((s) => s.stack === "total")).toBe(true);
    const pct = BUILDERS.stacked_bar(input(w("stacked_bar", { dimensions: ["build", "status"], measures: ["failed"] }, { options: { stack: "percent" } }), ["build", "status", "failed"], rows));
    const data = (pct.series as Array<{ data: number[] }>).map((s) => s.data);
    expect(data[0][0] + data[1][0]).toBeCloseTo(1);
    expect(data[0][0]).toBeCloseTo(0.75);
  });

  it("donut is a ring with one slice per category", () => {
    const opt = BUILDERS.donut(input(w("donut", { dimensions: ["status"], measures: ["failed"] }), ["status", "failed"], [["passed", 30], ["failed", 5]]));
    const s = (opt.series as Array<{ type: string; radius: string[]; data: Array<{ name: string }> }>)[0];
    expect(s.type).toBe("pie");
    expect(s.radius).toEqual(["45%", "70%"]);
    expect(s.data.map((d) => d.name)).toEqual(["passed", "failed"]);
  });

  it("pareto sorts descending and adds a cumulative share line", () => {
    const opt = BUILDERS.pareto(input(w("pareto", { dimensions: ["error_type"], measures: ["failed"] }), ["error_type", "failed"], [["b", 1], ["a", 3]]));
    const [bars, line] = opt.series as Array<{ data: number[] }>;
    expect(bars.data).toEqual([3, 1]);
    expect(line.data).toEqual([0.75, 1]);
  });

  it("histogram bins value counts", () => {
    const opt = BUILDERS.histogram(input(w("histogram", { dimensions: ["latency_ms"], measures: ["rows"] }, { options: { bins: 2 } }),
      ["latency_ms", "rows"], [[0, 1], [5, 2], [10, 3]]));
    expect((opt.series as Array<{ data: number[] }>)[0].data).toEqual([1, 5]);
    expect(binCounts([], "x", undefined, 5)).toEqual([]);
  });

  it("box maps the five quantile measures per category", () => {
    const opt = BUILDERS.box(input(w("box", { dimensions: ["suite"], measures: ["d_min", "d_p25", "d_p50", "d_p75", "d_max"] }, { encoding: { x: "suite", y: "duration_ms" } }),
      ["suite", "d_min", "d_p25", "d_p50", "d_p75", "d_max"], [["api", 1, 2, 3, 4, 5]]));
    expect((opt.series as Array<{ type: string; data: number[][] }>)[0]).toMatchObject({ type: "boxplot", data: [[1, 2, 3, 4, 5]] });
  });

  it("scatter plots raw pairs on value axes", () => {
    const opt = BUILDERS.scatter(input(w("scatter", { dimensions: ["bytes", "latency_ms"] }), ["bytes", "latency_ms"], [[10, 200], [20, 300]]));
    expect((opt.series as Array<{ data: number[][] }>)[0].data).toEqual([[10, 200], [20, 300]]);
    expect((opt.xAxis as { type: string }).type).toBe("value");
  });

  it("heatmap places cells by category index with a colour scale", () => {
    const opt = BUILDERS.heatmap(input(w("heatmap", { dimensions: ["case", "build"], measures: ["failed"] }), ["case", "build", "failed"],
      [["t1", "101", 1], ["t2", "102", 4]]));
    expect((opt.series as Array<{ data: number[][] }>)[0].data).toEqual([[0, 0, 1], [1, 1, 4]]);
    expect(opt.visualMap).toMatchObject({ min: 1, max: 4 });
  });

  it("table formats measures and keeps dimensions as text", () => {
    const model = tableModel(input(w("table", { dimensions: ["label"], measures: ["p95", "pass_rate"] }), ["label", "p95", "pass_rate"], [["GET /", 1500, 0.5]]));
    expect(model.header).toEqual(["label", "p95", "Pass rate"]);
    expect(model.cells).toEqual([["GET /", "1.5 s", "50%"]]);
  });

  it("every type has a builder and empty data shows a message", () => {
    for (const type of Object.keys(BUILDERS) as Array<keyof typeof BUILDERS>) {
      const opt = chartOption(input(w(type, { dimensions: ["x"], measures: ["failed"] }), ["x", "failed"], []));
      expect((opt.graphic as Array<{ style: { text: string } }>)[0].style.text).toBe("No data");
    }
  });
});

describe("formatting", () => {
  it("formats numbers by measure format", () => {
    expect(formatValue(0.953, "percent")).toBe("95.3%");
    expect(formatValue(850, "duration_ms")).toBe("850 ms");
    expect(formatValue(2500, "duration_ms")).toBe("2.5 s");
    expect(formatValue(2048, "bytes")).toBe("2 KB");
    expect(formatValue(3.333, "rate")).toBe("3.33/s");
    expect(formatValue(1_234_567)).toBe("1.2M");
    expect(formatValue(null)).toBe("—");
  });

  it("shortens labels and times", () => {
    expect(shortLabel("x".repeat(30))).toHaveLength(18);
    expect(axisTime("2026-10-05T00:00:00+00:00")).toBe("2026-10-05");
    expect(axisTime("2026-10-05T14:03:00+00:00")).toBe("10-05 14:03");
  });

  it("picks the threshold status", () => {
    expect(statusFor(0.5, [{ below: 0.9, color: "critical" }])).toBe("critical");
    expect(statusFor(0.95, [{ below: 0.9, color: "critical" }])).toBeNull();
    expect(statusFor(900, [{ above: 800, color: "warning" }])).toBe("warning");
  });
});

describe("thumbnails", () => {
  it("compact options hide axis labels, legends and pie labels", () => {
    const line = compactOption(BUILDERS.line(input(w("line", { dimensions: ["build", "suite"], measures: ["failed"] }), ["build", "suite", "failed"], [["1", "a", 1], ["1", "b", 2]])));
    expect(line.legend).toBeUndefined();
    expect((line.xAxis as { axisLabel: { show: boolean } }).axisLabel.show).toBe(false);
    const pie = compactOption(BUILDERS.donut(input(w("donut", { dimensions: ["s"], measures: ["failed"] }), ["s", "failed"], [["a", 1]])));
    expect((pie.series as Array<{ label: { show: boolean } }>)[0].label.show).toBe(false);
  });
});
