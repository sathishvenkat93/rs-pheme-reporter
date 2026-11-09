import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockFetch } from "../test/fetchMock";
import { plan, session } from "../test/samples";
import { makeQueryClient } from "./queryClient";
import { routes } from "./routes";

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <QueryClientProvider client={makeQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

afterEach(() => vi.unstubAllGlobals());

describe("app shell", () => {
  it("starts a session at / and shows the chat and canvas panes", async () => {
    mockFetch({
      "POST /sessions": session("INGEST", { id: "abc", plan_id: null }),
      "GET /sessions/abc/messages": [],
      "GET /sessions/abc": session("INGEST", { id: "abc", plan_id: null }),
    });
    const router = renderAt("/");
    await waitFor(() => expect(router.state.location.pathname).toBe("/s/abc"));
    expect(await screen.findByRole("region", { name: "Chat" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Canvas" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Upload files/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Message Pheme")).toBeDisabled();
  });

  it("shows proposed charts once the session has a plan", async () => {
    mockFetch({
      "GET /sessions/s1/messages": [],
      "GET /sessions/s1": session("PROPOSE"),
      "GET /plans/p1": plan(),
      "POST /query": { columns: ["suite", "rows"], rows: [["api", 3]], query_hash: "h", truncated: false },
    });
    renderAt("/s/s1");
    expect(await screen.findByRole("article", { name: "Chart o1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve plan" })).toBeEnabled();
  });

  it("navigates to the library from the main nav", async () => {
    mockFetch({ "POST /sessions": session("INGEST"), "GET /dashboards": [] });
    const router = renderAt("/library");
    await userEvent.click(screen.getByRole("link", { name: "New session" }));
    await waitFor(() => expect(router.state.location.pathname).not.toBe("/library"));
  });
});
