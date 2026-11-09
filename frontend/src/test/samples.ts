import type { DraftPlan, Session, VisualOption } from "../api/types";

export const option = (id: string, status: VisualOption["status"], extra: Partial<VisualOption> = {}): VisualOption => ({
  id, title: `Chart ${id}`, chart_type: "bar", fields: ["suite"], status, rationale: "because",
  query: { dataset: "ds", dimensions: ["suite"], measures: ["rows"] },
  measures: [{ id: "rows", expr: "count()" }],
  score: { data_fit: 0.9, intent: 0, insight: 0.5, perception: 0.9, domain_prior: 1, redundancy: 0, clutter: 0, total: 0.64, facts: ["3 suites"] },
  ...extra,
}) as VisualOption;

export const session = (state: Session["state"], extra: Partial<Session> = {}): Session =>
  ({ id: "s1", state, plan_id: "p1", dashboard_id: null, dataset_ids: ["d1"], ...extra }) as Session;

export const plan = (extra: Partial<DraftPlan> = {}): DraftPlan =>
  ({ id: "p1", session_id: "s1", version: 2, options: [option("o1", "recommended"), option("o2", "optional")],
     requests: [], decisions: [], measures: [], filters: [], layout_hints: [], ...extra }) as DraftPlan;
