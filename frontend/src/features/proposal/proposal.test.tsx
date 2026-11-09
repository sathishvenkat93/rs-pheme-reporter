import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { makeQueryClient } from "../../app/queryClient";
import { mockFetch } from "../../test/fetchMock";
import { option, plan, session } from "../../test/samples";
import { ApprovalCard } from "./ApprovalCard";
import { approvalState } from "./approval";
import { OptionCard } from "./OptionCard";

afterEach(() => vi.unstubAllGlobals());

const openDecision = { id: "D1", item: "req_pie", reason: "34 case values are too many slices for a pie.",
  options: ["accept_alternative", "keep_as_asked", "drop"], choice: null, before: { chart_type: "pie" }, after: { chart_type: "bar", top_n: 10 } } as const;

describe("approval button logic", () => {
  it("is enabled with charts and no open decisions", () => {
    expect(approvalState(session("REFINE"), plan())).toEqual({ enabled: true, reason: null, openItems: [] });
  });

  it("names the open decisions when disabled", () => {
    const p = plan({ options: [option("req_pie", "needs_approval", { title: "Pie of failures" })], decisions: [openDecision as never] });
    const state = approvalState(session("REFINE"), p);
    expect(state.enabled).toBe(false);
    expect(state.openItems).toEqual(["Pie of failures"]);
    expect(state.reason).toBe("Decide first: Pie of failures");
  });

  it("is disabled with nothing included, outside planning, or without a plan", () => {
    expect(approvalState(session("REFINE"), plan({ options: [option("o", "excluded")] })).reason).toBe("Include at least one chart.");
    expect(approvalState(session("APPROVED"), plan()).reason).toBe("Already approved.");
    expect(approvalState(session("PUBLISHED"), plan()).enabled).toBe(false);
    expect(approvalState(undefined, undefined).enabled).toBe(false);
  });

  it("uses the request text for decisions on not-feasible requests", () => {
    const p = plan({ requests: [{ id: "r1", raw_text: "3D globe of traffic" } as never],
      decisions: [{ ...openDecision, item: "r1", options: ["drop", "provide_data"] } as never] });
    expect(approvalState(session("REFINE"), p).openItems).toEqual(["3D globe of traffic"]);
  });
});

describe("cards", () => {
  it("option card has accessible names and toggles", async () => {
    mockFetch({ "POST /query": { columns: ["suite", "rows"], rows: [["api", 1]], query_hash: "h", truncated: false } });
    const onToggle = vi.fn();
    render(<QueryClientProvider client={makeQueryClient()}><OptionCard option={option("o1", "recommended")} datasetId="d1" onToggle={onToggle} /></QueryClientProvider>);
    expect(screen.getByRole("article", { name: "Chart o1" })).toBeInTheDocument();
    expect(screen.getByLabelText(/Status recommended. Score: total 0.64/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: "Include Chart o1" }));
    expect(onToggle).toHaveBeenCalledWith(false);
    expect(await screen.findByRole("img", { name: /Chart o1/ })).toBeInTheDocument();
  });

  it("needs-approval options can't be toggled until decided", () => {
    render(<QueryClientProvider client={makeQueryClient()}><OptionCard option={option("o", "needs_approval")} datasetId={undefined} onToggle={() => {}} /></QueryClientProvider>);
    expect(screen.getByRole("checkbox", { name: "Include Chart o" })).toBeDisabled();
  });

  it("approval card shows before/after and sends the choice", async () => {
    const onDecide = vi.fn();
    render(<ApprovalCard decision={openDecision as never} title="Pie of failures by test" onDecide={onDecide} />);
    expect(screen.getByText("chart type: pie")).toBeInTheDocument();
    expect(screen.getByText("chart type: bar, top n: 10")).toBeInTheDocument();
    const group = screen.getByRole("group", { name: "Choices for Pie of failures by test" });
    expect(group.querySelectorAll("button")).toHaveLength(3);
    await userEvent.click(screen.getByRole("button", { name: "Keep as asked" }));
    expect(onDecide).toHaveBeenCalledWith("keep_as_asked");
  });

  it("clarification decisions offer their candidates", async () => {
    const onDecide = vi.fn();
    render(<ApprovalCard decision={{ ...openDecision, options: ["choose_candidate", "drop"], candidates: ["p95", "throughput"] } as never}
                         title="Show performance" onDecide={onDecide} />);
    await userEvent.click(screen.getByRole("button", { name: "throughput" }));
    expect(onDecide).toHaveBeenCalledWith("choose_candidate", "throughput");
  });
});
