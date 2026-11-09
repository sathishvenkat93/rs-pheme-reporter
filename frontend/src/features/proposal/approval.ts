import type { DraftPlan, Session, VisualOption } from "../../api/types";

export const INCLUDED = new Set(["recommended", "requested", "needs_approval"]);

export interface ApprovalState {
  enabled: boolean;
  reason: string | null; // tooltip when disabled
  openItems: string[];
}

/** The "Approve plan" button: disabled while any decision is open (it names them). */
export function approvalState(session: Session | undefined, plan: DraftPlan | undefined): ApprovalState {
  if (!session || !plan) return { enabled: false, reason: "No plan yet.", openItems: [] };
  if (!["PROPOSE", "REFINE"].includes(session.state)) {
    return { enabled: false, reason: session.state === "APPROVED" ? "Already approved." : "Nothing to approve now.", openItems: [] };
  }
  const titles = new Map<string, string>([
    ...(plan.options ?? []).map((o): [string, string] => [o.id, o.title]),
    ...(plan.requests ?? []).map((r): [string, string] => [r.id, r.raw_text]),
  ]);
  const openItems = (plan.decisions ?? []).filter((d) => !d.choice).map((d) => titles.get(d.item) ?? d.item);
  if (openItems.length) {
    return { enabled: false, reason: `Decide first: ${openItems.join(", ")}`, openItems };
  }
  if (!(plan.options ?? []).some((o) => INCLUDED.has(o.status))) {
    return { enabled: false, reason: "Include at least one chart.", openItems: [] };
  }
  return { enabled: true, reason: null, openItems: [] };
}

export function visibleOptions(plan: DraftPlan | undefined): VisualOption[] {
  return (plan?.options ?? []).filter((o) => !o.id.endsWith("_asked") || o.status === "requested");
}
