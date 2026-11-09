import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { UNAUTHORIZED } from "../api/client";
import { mockFetch } from "../test/fetchMock";
import { LoginGate } from "./LoginGate";

afterEach(() => vi.unstubAllGlobals());

describe("login gate", () => {
  it("opens on a 401 and signs in by email", async () => {
    const calls = mockFetch({ "POST /auth/login": { token: "t" } });
    const reload = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload });
    render(<LoginGate />);
    expect(screen.queryByRole("dialog")).toBeNull();
    act(() => { window.dispatchEvent(new Event(UNAUTHORIZED)); });
    await userEvent.type(screen.getByLabelText("Work email"), "ann@alpha.test");
    await userEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(calls[0]).toMatchObject({ method: "POST", url: "/api/auth/login", body: { email: "ann@alpha.test" } });
    expect(reload).toHaveBeenCalled();
  });
});
