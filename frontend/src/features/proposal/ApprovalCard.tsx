import type { Decision, DecisionChoice } from "../../api/types";

const LABELS: Record<string, string> = {
  accept_alternative: "Accept alternative",
  keep_as_asked: "Keep as asked",
  drop: "Drop",
  provide_data: "Provide more data",
};

function describe(v: Record<string, unknown> | null | undefined): string {
  if (!v) return "";
  return Object.entries(v).map(([k, x]) => `${k.replace("_", " ")}: ${String(x)}`).join(", ");
}

export function ApprovalCard({ decision, title, onDecide }: {
  decision: Decision;
  title: string;
  onDecide: (choice: DecisionChoice, detail?: string) => void;
}) {
  return (
    <article aria-label={`Decision needed: ${title}`} className="rounded-lg border border-amber-300 bg-amber-50/60 p-3 text-sm">
      <h3 className="font-medium">{title}</h3>
      <p className="mt-1 text-ink-muted">{decision.reason}</p>
      {(decision.before || decision.after) && (
        <dl className="mt-2 grid grid-cols-2 gap-2 text-xs">
          <div><dt className="text-ink-muted">You asked for</dt><dd>{describe(decision.before)}</dd></div>
          <div><dt className="text-ink-muted">Pheme suggests</dt><dd>{describe(decision.after)}</dd></div>
        </dl>
      )}
      <div className="mt-2 flex flex-wrap gap-2" role="group" aria-label={`Choices for ${title}`}>
        {decision.options.filter((o) => o !== "choose_candidate").map((o) => (
          <button key={o} type="button" onClick={() => onDecide(o)}
                  className={`rounded px-2 py-1 text-xs ${o === "accept_alternative" ? "bg-accent text-white" : "border border-border bg-surface hover:border-accent"}`}>
            {LABELS[o] ?? o}
          </button>
        ))}
        {decision.options.includes("choose_candidate") && decision.candidates?.map((c) => (
          <button key={c} type="button" onClick={() => onDecide("choose_candidate", c)}
                  className="rounded-full border border-accent px-2 py-1 text-xs text-accent">
            {c.replaceAll("_", " ")}
          </button>
        ))}
      </div>
    </article>
  );
}
