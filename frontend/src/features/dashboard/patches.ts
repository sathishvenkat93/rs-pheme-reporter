// Pure helpers: turn direct manipulation into widget-id patch ops, and save with 409 handling.
import { ApiError } from "../../api/client";
import type { DashboardSpec, DashboardVersion, LayoutItem, PatchOp } from "../../api/types";

export interface GridItem {
  i: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Ops for every layout item whose position or size changed (drag and resize). */
export function layoutPatches(before: LayoutItem[], after: readonly GridItem[]): PatchOp[] {
  const old = new Map(before.map((i) => [i.widget, i]));
  const ops: PatchOp[] = [];
  for (const item of after) {
    const prev = old.get(item.i);
    if (!prev) continue;
    for (const key of ["x", "y", "w", "h"] as const) {
      if (prev[key] !== item[key]) ops.push({ op: "replace", path: `/layout/items/${item.i}/${key}`, value: item[key] });
    }
  }
  return ops;
}

export function renamePatch(widgetId: string, title: string): PatchOp[] {
  const t = title.trim();
  return t ? [{ op: "replace", path: `/widgets/${widgetId}/title`, value: t.slice(0, 200) }] : [];
}

/** The widget ids an op touches (widget or its layout item), or "*" for dashboard-level paths. */
export function touched(op: PatchOp): string {
  const m = /^\/(?:widgets|layout\/items)\/([^/]+)/.exec(op.path);
  return m ? m[1] : "*";
}

function part(spec: DashboardSpec, id: string): string {
  if (id === "*") return JSON.stringify({ title: spec.title, filters: spec.filters, measures: spec.measures });
  return JSON.stringify([spec.widgets?.find((w) => w.id === id) ?? null, spec.layout?.items?.find((i) => i.widget === id) ?? null]);
}

/** After a 409: the ops can be re-applied if nothing they touch changed between base and latest. */
export function canRebase(ops: PatchOp[], base: DashboardSpec, latest: DashboardSpec): { ok: boolean; changed: string[] } {
  const ids = [...new Set(ops.map(touched))];
  const changed = ids.filter((id) => part(base, id) !== part(latest, id));
  return { ok: changed.length === 0, changed };
}

export interface SaveDeps {
  patch: (id: string, base: number, ops: PatchOp[], note: string) => Promise<DashboardVersion>;
  latest: (id: string) => Promise<DashboardVersion>;
}

export type SaveResult =
  | { status: "saved"; version: DashboardVersion; rebased: boolean }
  | { status: "conflict"; message: string; latest: DashboardVersion }
  | { status: "invalid"; message: string };

/** PATCH with the base version; on 409 refetch and reapply if the touched widgets are unchanged. */
export async function saveOps(deps: SaveDeps, dashboardId: string, base: DashboardVersion, ops: PatchOp[], note: string): Promise<SaveResult> {
  try {
    return { status: "saved", version: await deps.patch(dashboardId, base.version, ops, note), rebased: false };
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    if (e.status !== 409) return { status: "invalid", message: e.message };
  }
  const latest = await deps.latest(dashboardId);
  const check = canRebase(ops, base.spec, latest.spec);
  if (!check.ok) {
    const names = check.changed.map((id) => (id === "*" ? "the dashboard settings" : latest.spec.widgets?.find((w) => w.id === id)?.title ?? id));
    return { status: "conflict", latest, message: `Someone else changed ${names.join(", ")} meanwhile, so your change wasn't applied.` };
  }
  try {
    return { status: "saved", version: await deps.patch(dashboardId, latest.version, ops, note), rebased: true };
  } catch (e) {
    if (e instanceof ApiError) return { status: "conflict", latest, message: e.message };
    throw e;
  }
}
