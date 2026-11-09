import { describe, expect, it } from "vitest";
import { initialLive, reduce, staleKeys } from "./timeline";

describe("live timeline", () => {
  it("tracks a send, streamed text and the reply", () => {
    let s = reduce(initialLive, { type: "sent", id: "1", text: "hi" });
    expect(s.pending).toBe(true);
    s = reduce(s, { type: "event", event: "tool_progress", data: { tool: "check_feasibility" } });
    expect(s.progress).toBe("Checking it against the data…");
    s = reduce(s, { type: "event", event: "assistant_delta", data: { text: "Hel" } });
    s = reduce(s, { type: "event", event: "assistant_delta", data: { text: "lo" } });
    expect(s.streaming).toBe("Hello");
    s = reduce(s, { type: "replied", id: "2", text: "Hello" });
    expect(s.pending).toBe(false);
    expect(s.streaming).toBe("");
    expect(s.items.map((i) => i.kind)).toEqual(["user", "assistant"]);
  });

  it("adds plan diffs, ignores empty ones, and holds confirm and edit previews", () => {
    let s = reduce(initialLive, { type: "event", event: "plan_diff", data: { version: 3, changes: ["Added Pie", "1 item needs approval"] } });
    s = reduce(s, { type: "event", event: "plan_diff", data: { version: 4, changes: ["No changes"] } });
    expect(s.items).toEqual([{ kind: "diff", id: "diff-3", changes: ["Added Pie", "1 item needs approval"] }]);
    s = reduce(s, { type: "event", event: "approval_confirm", data: { plan_version: 3, charts: 8 } });
    expect(s.confirm).toEqual({ plan_version: 3, charts: 8 });
    s = reduce(s, { type: "event", event: "state", data: { state: "APPROVED" } });
    expect(s.confirm).toBeNull();
    s = reduce(s, { type: "event", event: "edit_preview", data: { base_version: 1, note: "x", widgets: [], changes: [] } });
    expect(s.preview?.base_version).toBe(1);
    s = reduce(s, { type: "event", event: "dashboard", data: { version: 2 } });
    expect(s.preview).toBeNull();
  });

  it("deltas outside a pending reply are ignored", () => {
    expect(reduce(initialLive, { type: "event", event: "assistant_delta", data: { text: "x" } }).streaming).toBe("");
  });

  it("maps events to the queries they invalidate", () => {
    expect(staleKeys("plan_diff")).toContain("plan");
    expect(staleKeys("dashboard")).toEqual(expect.arrayContaining(["dashboard", "data", "versions"]));
    expect(staleKeys("assistant_delta")).toEqual([]);
  });
});
