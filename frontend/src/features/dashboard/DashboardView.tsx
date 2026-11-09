import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState, type ReactNode } from "react";
import GridLayout, { useContainerWidth, type Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import { Link } from "react-router";
import { api } from "../../api/client";
import type { DashboardVersion, PatchOp } from "../../api/types";
import { useSession } from "../session/SessionContext";
import { download, exportPng } from "./exportPng";
import { FilterBar } from "./FilterBar";
import { HistoryDrawer } from "./HistoryDrawer";
import { layoutPatches, renamePatch, saveOps } from "./patches";
import { RefreshPanel } from "./RefreshPanel";
import { WidgetCard } from "./WidgetCard";

const ROW_HEIGHT = 60;

export function DashboardView({ dashboardId, version }: { dashboardId: string; version?: number }) {
  const qc = useQueryClient();
  const { selected, setSelected } = useSession();
  const [filters, setFilters] = useState<Record<string, string[]>>({});
  const [history, setHistory] = useState(false);
  const [refresh, setRefresh] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const dash = useQuery({ queryKey: ["dashboard", dashboardId, version ?? "latest"], queryFn: () => api.dashboard(dashboardId, version) });
  const data = useQuery({
    queryKey: ["data", dashboardId, version ?? "latest", filters, dash.data?.version],
    queryFn: () => api.data(dashboardId, filters, version),
    enabled: !!dash.data,
  });
  const readOnly = version !== undefined;

  const layout: Layout = useMemo(
    () => (dash.data?.spec.layout?.items ?? []).map((i) => ({ i: i.widget, x: i.x, y: i.y, w: i.w, h: i.h, minW: 2, minH: 2 })),
    [dash.data],
  );

  function invalidate() {
    for (const key of ["dashboard", "data", "versions", "library"]) void qc.invalidateQueries({ queryKey: [key] });
  }

  async function save(ops: PatchOp[], note: string) {
    if (!dash.data || !ops.length) return;
    setNotice(null);
    const res = await saveOps({ patch: api.patch, latest: (id) => api.dashboard(id) }, dashboardId, dash.data as DashboardVersion, ops, note);
    if (res.status === "saved") { if (res.rebased) setNotice("The dashboard had changed; your edit was re-applied on the latest version."); }
    else setNotice(res.message);
    invalidate();
  }

  if (dash.isLoading) return <p className="p-6 text-sm text-ink-muted">Loading the dashboard…</p>;
  if (dash.isError || !dash.data) return <p role="alert" className="p-6 text-sm text-red-700">The dashboard couldn't be loaded.</p>;
  const { spec } = dash.data;
  const results = data.data ?? {};
  const toggle = (id: string, additive: boolean) =>
    setSelected(additive ? (selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id]) : selected.length === 1 && selected[0] === id ? [] : [id]);

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-border bg-surface px-4 py-2">
        <h2 className="text-sm font-semibold" data-testid="dashboard-title">{spec.title}</h2>
        <span className="text-xs text-ink-muted" data-testid="dashboard-version">v{dash.data.version}</span>
        <FilterBar spec={spec} filters={filters} onChange={setFilters} />
        <div className="ml-auto flex items-center gap-2 text-sm">
          {!readOnly && spec.datasets[0]?.mode === "live" && (
            <button type="button" className="rounded border border-border px-2 py-1 hover:border-accent" onClick={() => setRefresh((v) => !v)}>Upload new data</button>
          )}
          <button type="button" className="rounded border border-border px-2 py-1 hover:border-accent"
                  onClick={async () => download(await exportPng(spec), `${spec.title}.png`)}>Export PNG</button>
          <button type="button" className="rounded border border-border px-2 py-1 hover:border-accent" onClick={() => setHistory((v) => !v)}
                  aria-expanded={history}>History</button>
        </div>
      </div>
      {readOnly && (
        <div className="flex items-center gap-3 bg-amber-50 px-4 py-1.5 text-xs" role="status">
          Viewing version {version} (read-only).
          <Link className="text-accent underline" to={`/d/${dashboardId}`}>Back to latest</Link>
        </div>
      )}
      {notice && <p role="alert" className="bg-amber-50 px-4 py-1.5 text-xs">{notice}</p>}
      {refresh && spec.datasets[0] && (
        <div className="px-4 pt-3"><RefreshPanel dashboardId={dashboardId} datasetId={spec.datasets[0].dataset_id} onDone={invalidate} /></div>
      )}
      <div className="min-h-0 min-w-0 flex-1 overflow-y-auto bg-surface-muted p-2" aria-label="Dashboard canvas">
        <Canvas layout={layout} readOnly={readOnly} onLayoutStop={(next, note) => void save(layoutPatches(spec.layout?.items ?? [], next), note)}>
          {(spec.widgets ?? []).map((w) => (
            <div key={w.id}>
              <WidgetCard
                widget={w}
                result={results[w.id]}
                loading={data.isLoading}
                measures={(spec.measures ?? []).filter((m) => (w.query.measures ?? []).includes(m.id))}
                selected={selected.includes(w.id)}
                readOnly={readOnly}
                onSelect={(additive) => toggle(w.id, additive)}
                onRename={(title) => void save(renamePatch(w.id, title), `Renamed to ${title}`)}
              />
            </div>
          ))}
        </Canvas>
      </div>
      {history && <HistoryDrawer dashboardId={dashboardId} current={dash.data.version} onClose={() => setHistory(false)} />}
    </div>
  );
}

/** The grid, mounted only once there is a spec, so the width hook measures a real element. */
function Canvas({ layout, readOnly, onLayoutStop, children }: {
  layout: Layout;
  readOnly: boolean;
  onLayoutStop: (next: Layout, note: string) => void;
  children: ReactNode;
}) {
  const { width, containerRef, mounted } = useContainerWidth({ measureBeforeMount: true });
  return (
    <div ref={containerRef} className="w-full min-w-0">
      {mounted && (
        <GridLayout
          width={width}
          layout={layout}
          gridConfig={{ cols: 12, rowHeight: ROW_HEIGHT, margin: [10, 10] }}
          dragConfig={{ enabled: !readOnly, handle: ".drag-handle" }}
          resizeConfig={{ enabled: !readOnly }}
          onDragStop={(next) => onLayoutStop(next, "Moved a widget")}
          onResizeStop={(next) => onLayoutStop(next, "Resized a widget")}
        >
          {children}
        </GridLayout>
      )}
    </div>
  );
}
