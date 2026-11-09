import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { makeQueryClient } from "../../app/queryClient";
import { mockFetch } from "../../test/fetchMock";
import { LibraryPage } from "./LibraryPage";
import { sortDashboards } from "./pins";

const d = (id: string, title: string, updated: string, domain = "test_execution") =>
  ({ id, title, domain, tags: [domain], owner_id: "u", version: 1, updated_at: updated });

beforeEach(() => localStorage.clear());
afterEach(() => vi.unstubAllGlobals());

describe("library", () => {
  it("sorts pinned first, then by last update", () => {
    const items = [d("a", "A", "2026-01-01"), d("b", "B", "2026-03-01"), d("c", "C", "2026-02-01")];
    expect(sortDashboards(items, []).map((x) => x.id)).toEqual(["b", "c", "a"]);
    expect(sortDashboards(items, ["a"]).map((x) => x.id)).toEqual(["a", "b", "c"]);
  });

  it("searches, filters by domain and pins", async () => {
    const calls = mockFetch({
      "GET /dashboards": (url: string) => (url.includes("q=perf") ? [d("p", "Perf run", "2026-02-01", "performance")]
        : [d("t", "Tests", "2026-01-01"), d("p", "Perf run", "2026-02-01", "performance")]),
    });
    render(<QueryClientProvider client={makeQueryClient()}><MemoryRouter><LibraryPage /></MemoryRouter></QueryClientProvider>);
    const list = await screen.findByRole("list", { name: "Dashboards" });
    expect(await within(list).findAllByRole("listitem")).toHaveLength(2);
    await userEvent.click(within(list).getByRole("button", { name: "Pin Tests" }));
    expect(within(list).getAllByRole("link", { name: /^Tests|^Perf run/ })[0]).toHaveTextContent("Tests");
    expect(JSON.parse(localStorage.getItem("pheme.pins") ?? "[]")).toEqual(["t"]);
    await userEvent.type(screen.getByLabelText("Search dashboards"), "perf");
    expect(await within(list).findByText("Perf run")).toBeInTheDocument();
    expect(calls.some((c) => c.url.includes("q=perf"))).toBe(true);
    await userEvent.selectOptions(screen.getByLabelText("Filter by domain"), "performance");
    expect(calls.some((c) => c.url.includes("domain=performance"))).toBe(true);
  });
});
