// Pure mappings: (widget spec, rows) → ECharts option. One function per widget type.
import type { EChartsOption, SeriesOption } from "echarts";
import type { Measure, QueryResult, Threshold, Widget, WidgetType } from "../api/types";
import { axisTime, formatValue, shortLabel, type MeasureFormat } from "./format";
import { theme } from "./theme";

export interface ChartInput {
  widget: Widget;
  result: Pick<QueryResult, "columns" | "rows">;
  measures: Measure[];
}

type Row = Record<string, unknown>;

export function toRecords(result: ChartInput["result"]): Row[] {
  return result.rows.map((r) => Object.fromEntries(result.columns.map((c, i) => [c, r[i]])));
}

const fmtOf = (input: ChartInput, id: string | undefined): MeasureFormat =>
  (input.measures.find((m) => m.id === id)?.format as MeasureFormat) ?? null;

const labelOf = (input: ChartInput, id: string): string =>
  input.measures.find((m) => m.id === id)?.label ?? id.replaceAll("_", " ");

const base = (): EChartsOption => ({
  color: [...theme.series],
  textStyle: { fontFamily: theme.font, color: theme.ink },
  grid: { left: 48, right: 24, top: 28, bottom: 40, containLabel: true },
  tooltip: { trigger: "axis", confine: true },
  animation: false,
});

export function emptyOption(message = "No data"): EChartsOption {
  return {
    ...base(),
    graphic: [{ type: "text", left: "center", top: "middle", style: { text: message, fill: theme.muted, fontSize: 14 } }],
  };
}

export function statusFor(value: number, thresholds: Threshold[] = []): "good" | "warning" | "critical" | null {
  let hit: Threshold | undefined;
  for (const t of thresholds) {
    if ((t.below != null && value < t.below) || (t.above != null && value > t.above)) hit = t;
  }
  return hit?.color ?? null;
}

function markLines(widget: Widget): SeriesOption["markLine"] | undefined {
  const data: Array<Record<string, unknown>> = [];
  for (const t of widget.thresholds ?? []) {
    const y = t.below ?? t.above;
    data.push({ yAxis: y, lineStyle: { color: theme.status[t.color], type: "dashed" }, label: { formatter: t.color } });
  }
  for (const a of widget.annotations ?? []) {
    if (a.type !== "rule") continue;
    if (a.y != null) data.push({ yAxis: a.y, label: { formatter: a.label ?? "" }, lineStyle: { color: theme.muted } });
    else if (a.x != null) data.push({ xAxis: String(a.x), label: { formatter: a.label ?? "" }, lineStyle: { color: theme.muted } });
  }
  if (widget.target != null) data.push({ yAxis: widget.target, label: { formatter: "target" }, lineStyle: { color: theme.status.good } });
  return data.length ? { symbol: "none", silent: true, data } : undefined;
}

function categories(records: Row[], key: string): string[] {
  return [...new Set(records.map((r) => String(r[key])))];
}

// ----------------------------------------------------------------------------- per type

export function kpiOption(input: ChartInput): EChartsOption {
  const m = input.widget.query.measures?.[0];
  const rows = toRecords(input.result);
  if (!m || !rows.length) return emptyOption();
  const value = Number(rows[0][m]);
  const status = Number.isFinite(value) ? statusFor(value, input.widget.thresholds) : null;
  const target = input.widget.target;
  return {
    ...base(),
    tooltip: { show: false },
    graphic: [
      {
        type: "text", left: "center", top: "32%",
        style: { text: formatValue(value, fmtOf(input, m)), fontSize: 34, fontWeight: 600, fill: status ? theme.status[status] : theme.ink },
      },
      ...(target != null
        ? [{ type: "text" as const, left: "center", top: "68%",
             style: { text: `target ${formatValue(target, fmtOf(input, m))}`, fontSize: 12, fill: theme.muted } }]
        : []),
    ],
  };
}

function xySeries(input: ChartInput, kind: "line" | "bar", extra: Partial<SeriesOption> = {}): EChartsOption {
  const { widget } = input;
  const dims = widget.query.dimensions ?? [];
  const measures = widget.query.measures ?? [];
  const rows = toRecords(input.result);
  if (!rows.length || !dims.length || !measures.length) return emptyOption();
  const x = dims[0];
  const xs = categories(rows, x);
  const isTime = /timestamp|date|_at$|^ts$/.test(x);
  const horizontal = kind === "bar" && widget.options?.horizontal === true;
  const fmt = fmtOf(input, measures[0]);
  let series: SeriesOption[];
  if (dims.length > 1) {
    const split = dims[1];
    series = categories(rows, split).map((s) => ({
      type: kind, name: shortLabel(s, 24),
      data: xs.map((v) => {
        const r = rows.find((row) => String(row[x]) === v && String(row[split]) === s);
        return r ? (r[measures[0]] as number) : null;
      }),
      ...extra,
    })) as SeriesOption[];
  } else {
    series = measures.map((m) => ({
      type: kind, name: labelOf(input, m), data: rows.map((r) => r[m] as number), ...extra,
    })) as SeriesOption[];
  }
  if (widget.options?.stack === "percent") {
    const totals = xs.map((_, i) => series.reduce((sum, s) => sum + Number((s.data as number[])[i] ?? 0), 0));
    series = series.map((s) => ({ ...s, stack: "total", data: (s.data as number[]).map((v, i) => (totals[i] ? (Number(v ?? 0) / totals[i]) : 0)) })) as SeriesOption[];
  } else if (widget.options?.stack === "stacked") {
    series = series.map((s) => ({ ...s, stack: "total" })) as SeriesOption[];
  }
  if (series.length) (series[0] as { markLine?: unknown }).markLine = markLines(widget);
  const valueFmt = widget.options?.stack === "percent" ? "percent" : fmt;
  const catAxis = {
    type: "category" as const, data: xs,
    axisLabel: { formatter: (v: string) => (isTime ? axisTime(v) : shortLabel(v)), hideOverlap: true },
  };
  const valAxis = {
    type: (widget.options?.log_scale ? "log" : "value") as "log" | "value",
    axisLabel: { formatter: (v: number) => formatValue(v, valueFmt) },
    splitLine: { lineStyle: { color: theme.grid } },
  };
  return {
    ...base(),
    legend: series.length > 1 ? { type: "scroll", bottom: 0 } : undefined,
    tooltip: { trigger: "axis", confine: true, valueFormatter: (v) => formatValue(v, valueFmt) },
    xAxis: horizontal ? valAxis : catAxis,
    yAxis: horizontal ? { ...catAxis, inverse: true } : valAxis,
    series,
  };
}

export const lineOption = (i: ChartInput) => xySeries(i, "line", { symbolSize: 6, connectNulls: true } as Partial<SeriesOption>);
export const areaOption = (i: ChartInput) => xySeries(i, "line", { areaStyle: { opacity: 0.25 }, symbol: "none" } as Partial<SeriesOption>);
export const barOption = (i: ChartInput) => xySeries(i, "bar", { barMaxWidth: 40 } as Partial<SeriesOption>);
export const stackedBarOption = (i: ChartInput) =>
  xySeries({ ...i, widget: { ...i.widget, options: { ...i.widget.options, stack: i.widget.options?.stack ?? "stacked" } } }, "bar");

export function paretoOption(input: ChartInput): EChartsOption {
  const dims = input.widget.query.dimensions ?? [];
  const m = input.widget.query.measures?.[0];
  const rows = toRecords(input.result).sort((a, b) => Number(b[m ?? ""]) - Number(a[m ?? ""]));
  if (!rows.length || !m || !dims.length) return emptyOption();
  const total = rows.reduce((s, r) => s + Number(r[m] ?? 0), 0) || 1;
  let running = 0;
  const cumulative = rows.map((r) => (running += Number(r[m] ?? 0)) / total);
  return {
    ...base(),
    tooltip: { trigger: "axis", confine: true },
    xAxis: { type: "category", data: rows.map((r) => String(r[dims[0]])), axisLabel: { formatter: (v: string) => shortLabel(v), hideOverlap: true } },
    yAxis: [
      { type: "value", axisLabel: { formatter: (v: number) => formatValue(v, fmtOf(input, m)) }, splitLine: { lineStyle: { color: theme.grid } } },
      { type: "value", min: 0, max: 1, axisLabel: { formatter: (v: number) => formatValue(v, "percent") }, splitLine: { show: false } },
    ],
    series: [
      { type: "bar", name: labelOf(input, m), data: rows.map((r) => r[m] as number), barMaxWidth: 40 },
      { type: "line", name: "Cumulative", yAxisIndex: 1, data: cumulative, symbolSize: 5, color: theme.status.warning },
    ],
  };
}

export function donutOption(input: ChartInput): EChartsOption {
  const dims = input.widget.query.dimensions ?? [];
  const m = input.widget.query.measures?.[0];
  const rows = toRecords(input.result);
  if (!rows.length || !m || !dims.length) return emptyOption();
  return {
    ...base(),
    tooltip: { trigger: "item", confine: true, valueFormatter: (v) => formatValue(v, fmtOf(input, m)) },
    legend: { type: "scroll", bottom: 0 },
    series: [{
      type: "pie", radius: ["45%", "70%"], avoidLabelOverlap: true,
      label: { formatter: (p: { name: string; percent?: number }) => `${shortLabel(p.name, 14)} ${Math.round(p.percent ?? 0)}%` },
      data: rows.map((r) => ({ name: String(r[dims[0]]), value: r[m] as number })),
    }],
  };
}

/** Bin (value, count) rows into ``bins`` equal-width buckets. */
export function binCounts(rows: Row[], valueKey: string, countKey: string | undefined, bins: number) {
  const values = rows.map((r) => Number(r[valueKey])).filter(Number.isFinite);
  if (!values.length) return [];
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const width = hi > lo ? (hi - lo) / bins : 1;
  const counts = new Array<number>(hi > lo ? bins : 1).fill(0);
  for (const r of rows) {
    const v = Number(r[valueKey]);
    if (!Number.isFinite(v)) continue;
    const i = Math.min(counts.length - 1, Math.floor((v - lo) / width));
    counts[i] += countKey ? Number(r[countKey] ?? 1) : 1;
  }
  return counts.map((c, i) => ({ from: lo + i * width, to: lo + (i + 1) * width, count: c }));
}

export function histogramOption(input: ChartInput): EChartsOption {
  const field = input.widget.query.dimensions?.[0];
  const countKey = input.widget.query.measures?.[0];
  const rows = toRecords(input.result);
  if (!field || !rows.length) return emptyOption();
  const fmt: MeasureFormat = /_ms$|latency|duration/.test(field) ? "duration_ms" : null;
  const buckets = binCounts(rows, field, countKey, input.widget.options?.bins ?? 30);
  return {
    ...base(),
    tooltip: { trigger: "axis", confine: true },
    xAxis: { type: "category", data: buckets.map((b) => formatValue(b.from, fmt)), axisLabel: { hideOverlap: true } },
    yAxis: { type: "value", splitLine: { lineStyle: { color: theme.grid } } },
    series: [{ type: "bar", name: "count", data: buckets.map((b) => b.count), barCategoryGap: "4%" }],
  };
}

export function boxOption(input: ChartInput): EChartsOption {
  const dims = input.widget.query.dimensions ?? [];
  const ms = input.widget.query.measures ?? [];
  const rows = toRecords(input.result);
  if (ms.length !== 5 || !rows.length) return emptyOption();
  const field = input.widget.encoding?.y ?? "";
  const fmt: MeasureFormat = /_ms$|latency|duration/.test(field) ? "duration_ms" : null;
  return {
    ...base(),
    tooltip: { trigger: "item", confine: true },
    xAxis: { type: "category", data: dims.length ? rows.map((r) => String(r[dims[0]])) : [field], axisLabel: { formatter: (v: string) => shortLabel(v) } },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => formatValue(v, fmt) }, splitLine: { lineStyle: { color: theme.grid } } },
    series: [{ type: "boxplot", name: field, data: rows.map((r) => ms.map((m) => Number(r[m]))) }],
  };
}

export function scatterOption(input: ChartInput): EChartsOption {
  const [x, y] = input.widget.query.dimensions ?? [];
  const rows = toRecords(input.result);
  if (!x || !y || !rows.length) return emptyOption();
  return {
    ...base(),
    tooltip: { trigger: "item", confine: true },
    xAxis: { type: "value", name: x, nameLocation: "middle", nameGap: 28, splitLine: { lineStyle: { color: theme.grid } } },
    yAxis: { type: "value", name: y, splitLine: { lineStyle: { color: theme.grid } } },
    series: [{ type: "scatter", symbolSize: 5, data: rows.map((r) => [Number(r[x]), Number(r[y])]) }],
  };
}

export function heatmapOption(input: ChartInput): EChartsOption {
  const [rowsDim, colsDim] = input.widget.query.dimensions ?? [];
  const m = input.widget.query.measures?.[0];
  const rows = toRecords(input.result);
  if (!rowsDim || !colsDim || !m || !rows.length) return emptyOption();
  const ys = categories(rows, rowsDim);
  const xs = categories(rows, colsDim);
  const values = rows.map((r) => Number(r[m] ?? 0));
  return {
    ...base(),
    tooltip: { trigger: "item", confine: true },
    grid: { left: 48, right: 24, top: 16, bottom: 56, containLabel: true },
    xAxis: { type: "category", data: xs, axisLabel: { formatter: (v: string) => shortLabel(v, 10), hideOverlap: true } },
    yAxis: { type: "category", data: ys, axisLabel: { formatter: (v: string) => shortLabel(v) } },
    visualMap: { min: Math.min(...values), max: Math.max(...values, 1), orient: "horizontal", left: "center", bottom: 0,
                 calculable: false, inRange: { color: ["#eef2ff", theme.accent] } },
    series: [{ type: "heatmap", data: rows.map((r) => [xs.indexOf(String(r[colsDim])), ys.indexOf(String(r[rowsDim])), Number(r[m] ?? 0)]) }],
  };
}

/** Tables render as HTML; this gives the formatted header and cells. */
export function tableModel(input: ChartInput): { header: string[]; cells: string[][] } {
  const { columns, rows } = input.result;
  const dims = new Set(input.widget.query.dimensions ?? []);
  return {
    header: columns.map((c) => (dims.has(c) ? c : labelOf(input, c))),
    cells: rows.map((r) => r.map((v, i) => (dims.has(columns[i]) ? shortLabel(v, 40) : formatValue(v, fmtOf(input, columns[i]))))),
  };
}

export const BUILDERS: Record<Exclude<WidgetType, "table">, (i: ChartInput) => EChartsOption> = {
  kpi: kpiOption,
  line: lineOption,
  area: areaOption,
  bar: barOption,
  stacked_bar: stackedBarOption,
  donut: donutOption,
  histogram: histogramOption,
  box: boxOption,
  scatter: scatterOption,
  heatmap: heatmapOption,
  pareto: paretoOption,
};

export function chartOption(input: ChartInput): EChartsOption {
  if (!input.result.rows.length) return emptyOption();
  return input.widget.type === "table" ? emptyOption() : BUILDERS[input.widget.type](input);
}

/** Thumbnail variant: no axis labels, legends or pie labels; a tight grid. Tooltips stay. */
export function compactOption(option: EChartsOption): EChartsOption {
  const axes = (a: unknown) =>
    Array.isArray(a) ? a.map((x) => ({ ...x, axisLabel: { show: false }, name: undefined, splitLine: { show: false } }))
      : a ? { ...(a as object), axisLabel: { show: false }, name: undefined, splitLine: { show: false } } : a;
  const series = Array.isArray(option.series)
    ? option.series.map((s) => (s.type === "pie" ? { ...s, label: { show: false }, radius: ["50%", "85%"] } : { ...s, markLine: undefined }))
    : option.series;
  return {
    ...option,
    legend: undefined,
    visualMap: option.visualMap ? { ...(option.visualMap as object), show: false } : undefined,
    grid: { left: 4, right: 4, top: 8, bottom: 4, containLabel: false },
    xAxis: axes(option.xAxis) as EChartsOption["xAxis"],
    yAxis: axes(option.yAxis) as EChartsOption["yAxis"],
    series: series as EChartsOption["series"],
  };
}
