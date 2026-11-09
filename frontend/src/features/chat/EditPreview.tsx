import { useSession } from "../session/SessionContext";

function Rows({ value }: { value: unknown }) {
  if (!Array.isArray(value)) return <span className="text-ink-muted">{value == null ? "—" : String(value)}</span>;
  return (
    <ul className="space-y-0.5">
      {value.slice(0, 5).map((r, i) => <li key={i} className="truncate">{Array.isArray(r) ? r.join(" · ") : String(r)}</li>)}
    </ul>
  );
}

/** The agent's proposed edit: before/after side by side, with Apply and Adjust. */
export function EditPreview() {
  const { live, session, applyEdit, discardEdit } = useSession();
  const pending = session?.pending_edit as { note?: string } | null | undefined;
  const p = live.preview ?? (pending ? { note: pending.note ?? "Edit waiting", widgets: [], base_version: 0, changes: [] } : null);
  if (!p) return null;
  return (
    <section aria-label="Proposed edit" className="rounded-lg border border-accent bg-accent/5 p-2 text-xs">
      <p className="font-medium">{p.note}</p>
      {p.widgets.map((w) => (
        <div key={w.id} className="mt-1 grid grid-cols-2 gap-2">
          <div><p className="text-ink-muted">Before · {w.id.replace(/^w_/, "")}</p><Rows value={w.before} /></div>
          <div><p className="text-ink-muted">After</p><Rows value={w.after} /></div>
        </div>
      ))}
      <div className="mt-2 flex gap-2">
        <button type="button" className="rounded bg-accent px-2 py-1 text-white" onClick={() => void applyEdit()}>Apply</button>
        <button type="button" className="rounded border border-border bg-surface px-2 py-1" onClick={() => void discardEdit()}>Adjust</button>
      </div>
    </section>
  );
}
