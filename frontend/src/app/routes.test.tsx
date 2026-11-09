import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RouterProvider, createMemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { routes } from "./routes";

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(<RouterProvider router={router} />);
  return router;
}

describe("app shell", () => {
  it("shows the chat and canvas panes on the workspace route", () => {
    renderAt("/");
    expect(screen.getByRole("region", { name: "Chat" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Canvas" })).toBeInTheDocument();
  });

  it("renders the library route", () => {
    renderAt("/library");
    expect(screen.getByRole("heading", { name: "Library" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Chat" })).not.toBeInTheDocument();
  });

  it("navigates to the library from the main nav", async () => {
    const router = renderAt("/");
    await userEvent.click(screen.getByRole("link", { name: "Library" }));
    expect(router.state.location.pathname).toBe("/library");
  });
});
