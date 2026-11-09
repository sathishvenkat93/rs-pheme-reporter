import type { VisualOption } from "../../api/types";

/** Score breakdown shown on hover/focus of an option's status badge. */
export function scoreSummary(o: VisualOption): string {
  const s = o.score;
  if (!s) return "No score";
  const parts = [`total ${s.total.toFixed(2)}`, `fit ${s.data_fit}`, `intent ${s.intent}`, `insight ${s.insight}`,
                 `perception ${s.perception}`, `domain ${s.domain_prior}`, `redundancy −${s.redundancy}`, `clutter −${s.clutter}`];
  return parts.join(" · ") + (s.facts?.length ? `\n${s.facts.join("; ")}` : "");
}
