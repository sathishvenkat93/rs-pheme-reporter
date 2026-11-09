import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";

export function HistoryDrawer({ dashboardId, current, onClose }: { dashboardId: string; current: number; onClose: () => void }) {
  const qc = useQueryClient();
  const versions = useQuery({ queryKey: ["versions", dashboardId], queryFn: () => api.versions(dashboardId) });
  const [diff, setDiff] = useState<{ n: number; ops: Array<{ op: string; path: string; value?: unknown }> } | null>(null);

  async function restore(n: number) {
    await api.restore(dashboardId, n);
    for (const key of ["dashboard", "versions", "data", "library"]) void qc.invalidateQueries({ queryKey: [key] });
  }

  return (
    <aside aria-label="Version history" className="absolute inset-y-0 right-0 z-10 flex w-96 flex-col border-l border-border bg-surface shadow-lg">
      <header className="flex items-center justify-between border-b border-border p-3">
        <h2 className="text-sm font-medium">Version history</h2>
        <button type="button" onClick={onClose} aria-label="Close history" className="text-ink-muted">✕</button>
      </header>
      <ol className="min-h-0 flex-1 overflow-y-auto">
        {[...(versions.data ?? [])].reverse().map((v) => (
          <li key={v.version} className="border-b border-border p-3 text-sm" aria-label={`Version ${v.version}`}>
            <div className="flex items-center justify-between">
              <span className="font-medium">v{v.version}{v.version === current && <span className="ml-1 text-xs text-accent">current</span>}</span>
              <time className="text-xs text-ink-muted" dateTime={v.created_at}>{new Date(v.created_at).toLocaleString()}</time>
            </div>
            <p className="text-xs text-ink-muted">{v.change_note ?? "—"} · by {v.author_id.slice(0, 8)}</p>
            <div className="mt-1 flex gap-3 text-xs">
              <Link className="text-accent underline" to={`/d/${dashboardId}/v/${v.version}`}>View</Link>
              {v.version !== current && (
                <>
                  <button type="button" className="text-accent underline"
                          onClick={async () => setDiff({ n: v.version, ops: await api.diff(dashboardId, v.version, current) })}>
                    Diff vs current
                  </button>
                  <button type="button" className="text-accent underline" onClick={() => void restore(v.version)}>Restore</button>
                </>
              )}
            </div>
            {diff?.n === v.version && (
              <ul className="mt-2 max-h-40 overflow-auto rounded bg-surface-muted p-2 font-mono text-[11px]" aria-label={`Changes from v${v.version}`}>
                {diff.ops.length === 0 && <li>No differences</li>}
                {diff.ops.map((o, i) => <li key={i}>{o.op} {o.path}{o.value !== undefined && ` → ${JSON.stringify(o.value).slice(0, 60)}`}</li>)}
              </ul>
            )}
          </li>
        ))}
      </ol>
    </aside>
  );
}
