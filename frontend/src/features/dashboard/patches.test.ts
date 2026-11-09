import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { DashboardSpec, DashboardVersion } from "../../api/types";
import { canRebase, layoutPatches, renamePatch, saveOps } from "./patches";

const spec = (over: Partial<DashboardSpec> = {}): DashboardSpec => ({
  dashboard_id: "d", title: "T",
  datasets: [{ id: "ds", dataset_id: "x", kind: "generic", schema_hash: "0".repeat(64), mode: "live" }],
  layout: { columns: 12, items: [{ widget: "w_a", x: 0, y: 0, w: 6, h: 4 }, { widget: "w_b", x: 6, y: 0, w: 6, h: 4 }] },
  widgets: [{ id: "w_a", type: "bar", title: "A", query: { dataset: "ds" } }, { id: "w_b", type: "line", title: "B", query: { dataset: "ds" } }],
  ...over,
}) as DashboardSpec;
const version = (n: number, s = spec()): DashboardVersion => ({ dashboard_id: "d", version: n, spec: s, author_id: "u", change_note: null, created_at: "" }) as DashboardVersion;

describe("patch generation", () => {
  it("drag produces x/y replaces for moved items only", () => {
    const ops = layoutPatches(spec().layout!.items!, [{ i: "w_a", x: 6, y: 4, w: 6, h: 4 }, { i: "w_b", x: 6, y: 0, w: 6, h: 4 }]);
    expect(ops).toEqual([
      { op: "replace", path: "/layout/items/w_a/x", value: 6 },
      { op: "replace", path: "/layout/items/w_a/y", value: 4 },
    ]);
  });

  it("resize produces w/h replaces", () => {
    const ops = layoutPatches(spec().layout!.items!, [{ i: "w_b", x: 6, y: 0, w: 4, h: 6 }]);
    expect(ops.map((o) => o.path)).toEqual(["/layout/items/w_b/w", "/layout/items/w_b/h"]);
  });

  it("no change, no ops; unknown items are ignored", () => {
    expect(layoutPatches(spec().layout!.items!, [{ i: "w_a", x: 0, y: 0, w: 6, h: 4 }, { i: "w_zzz", x: 1, y: 1, w: 1, h: 1 }])).toEqual([]);
  });

  it("rename trims and ignores empty titles", () => {
    expect(renamePatch("w_a", "  Build health ")).toEqual([{ op: "replace", path: "/widgets/w_a/title", value: "Build health" }]);
    expect(renamePatch("w_a", "   ")).toEqual([]);
  });
});

describe("409 handling", () => {
  const ops = renamePatch("w_a", "New");

  it("rebases onto the latest version when the touched widget is unchanged", async () => {
    const latest = version(3, spec({ widgets: [...spec().widgets!.slice(0, 1), { ...spec().widgets![1], title: "B changed" }] }));
    const patch = vi.fn()
      .mockRejectedValueOnce(new ApiError(409, "stale"))
      .mockResolvedValueOnce(version(4));
    const res = await saveOps({ patch, latest: async () => latest }, "d", version(2), ops, "rename");
    expect(res).toMatchObject({ status: "saved", rebased: true });
    expect(patch).toHaveBeenLastCalledWith("d", 3, ops, "rename");
  });

  it("reports a conflict when someone else changed the same widget", async () => {
    const latest = version(3, spec({ widgets: [{ ...spec().widgets![0], title: "Theirs" }, spec().widgets![1]] }));
    const patch = vi.fn().mockRejectedValueOnce(new ApiError(409, "stale"));
    const res = await saveOps({ patch, latest: async () => latest }, "d", version(2), ops, "rename");
    expect(res.status).toBe("conflict");
    expect(res.status === "conflict" && res.message).toContain("Theirs");
    expect(patch).toHaveBeenCalledTimes(1);
  });

  it("passes validation errors through", async () => {
    const patch = vi.fn().mockRejectedValueOnce(new ApiError(422, "A donut needs at most 6 parts."));
    expect(await saveOps({ patch, latest: vi.fn() }, "d", version(2), ops, "x")).toEqual({ status: "invalid", message: "A donut needs at most 6 parts." });
  });

  it("canRebase checks layout items and dashboard-level paths too", () => {
    const moved = spec({ layout: { columns: 12, items: [{ widget: "w_a", x: 6, y: 0, w: 6, h: 4 }, spec().layout!.items![1]] } });
    expect(canRebase([{ op: "replace", path: "/layout/items/w_a/x", value: 1 }], spec(), moved).ok).toBe(false);
    expect(canRebase([{ op: "replace", path: "/title", value: "x" }], spec(), spec({ title: "Other" })).changed).toEqual(["*"]);
  });
});
