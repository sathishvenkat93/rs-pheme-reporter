import { useSession } from "../session/SessionContext";
import { ApprovalCard } from "./ApprovalCard";
import { approvalState, visibleOptions } from "./approval";
import { OptionCard } from "./OptionCard";

export function ProposalPane() {
  const { session, plan, decide, toggle, approve, live, send } = useSession();
  if (!session || session.state === "INGEST" || session.state === "PROFILE" || !plan) {
    return (
      <div className="flex h-full items-center justify-center p-6 text-sm text-ink-muted">
        {session?.state === "PROFILE" ? "Profiling your data…" : "Upload a file to see proposed charts here."}
      </div>
    );
  }
  const approval = approvalState(session, plan);
  const titles = new Map([...(plan.options ?? []).map((o) => [o.id, o.title] as const), ...(plan.requests ?? []).map((r) => [r.id, r.raw_text] as const)]);
  const open = (plan.decisions ?? []).filter((d) => !d.choice);
  const options = visibleOptions(plan);
  const planning = session.state === "PROPOSE" || session.state === "REFINE";
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center justify-between border-b border-border bg-surface px-4 py-2">
        <h2 className="text-sm font-medium">Proposed dashboard <span className="text-ink-muted">· plan v{plan.version}</span></h2>
        <div className="flex items-center gap-2">
          {session.state === "APPROVED" && (
            <button type="button" className="rounded bg-accent px-3 py-1.5 text-sm text-white" onClick={() => void send("Build the dashboard")}>
              Build dashboard
            </button>
          )}
          <span title={approval.reason ?? "Approve this plan"}>
            <button type="button" disabled={!approval.enabled} onClick={() => void approve()}
                    aria-describedby={approval.reason ? "approve-reason" : undefined}
                    className="rounded bg-accent px-3 py-1.5 text-sm text-white disabled:cursor-not-allowed disabled:opacity-40">
              Approve plan
            </button>
          </span>
          {approval.reason && <span id="approve-reason" className="sr-only">{approval.reason}</span>}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {live.confirm && approval.enabled && (
          <div role="dialog" aria-label="Confirm approval" className="mb-4 flex items-center justify-between rounded-lg border border-accent bg-accent/5 p-3 text-sm">
            <span>Approve the plan with {live.confirm.charts} charts?</span>
            <button type="button" className="rounded bg-accent px-3 py-1 text-white" onClick={() => void approve()}>Approve</button>
          </div>
        )}
        {open.length > 0 && (
          <section aria-label="Decisions" className="mb-4 space-y-2">
            {open.map((d) => (
              <ApprovalCard key={d.id} decision={d} title={titles.get(d.item) ?? d.item} onDecide={(c, detail) => void decide(d.id, c, detail)} />
            ))}
          </section>
        )}
        <section aria-label="Charts" className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-3">
          {options.map((o) => (
            <OptionCard key={o.id} option={o} datasetId={session.dataset_ids?.[0]} disabled={!planning}
                        onToggle={(inc) => void toggle(o.id, inc)} />
          ))}
        </section>
      </div>
    </div>
  );
}
