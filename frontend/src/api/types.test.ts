import { describe, expect, it } from "vitest";
import type { DashboardSpec } from ".";

// The R2 example from the plan; `satisfies` makes tsc check it against the generated types.
const r2 = {
  dashboard_id: "dsh_7f3a",
  title: "Regression run — Sprint 42",
  datasets: [
    {
      id: "ds_tests",
      dataset_id: "6f1c3a52-9d1e-4c4b-9a7e-1f0e2d3c4b5a",
      kind: "test_results",
      schema_hash: "0".repeat(64),
      mode: "live",
      version_id: null,
    },
  ],
  measures: [{ id: "pass_rate", expr: "ratio(count_if(status == 'passed'), count())", format: "percent" }],
  layout: { columns: 12, items: [{ widget: "w_pass_kpi", x: 0, y: 0, w: 3, h: 2 }] },
  widgets: [
    {
      id: "w_pass_kpi",
      type: "kpi",
      title: "Pass rate",
      query: { dataset: "ds_tests", measures: ["pass_rate"] },
      target: 0.95,
      thresholds: [{ below: 0.9, color: "critical" }],
    },
  ],
} satisfies DashboardSpec;

describe("generated spec types", () => {
  it("accept the R2 example", () => {
    expect(r2.widgets[0].type).toBe("kpi");
  });
});
