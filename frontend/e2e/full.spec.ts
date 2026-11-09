// M14: the full end-to-end flow in one browser session (FakeLLM, runs in CI via make e2e).
import { expect, test } from "@playwright/test";
import { say, startWithJunit } from "./helpers";

test("full flow: infeasible request, approval, dashboard, chat edit, undo, library", async ({ page }) => {
  // Upload → options
  await startWithJunit(page);
  const charts = page.getByRole("region", { name: "Charts" });
  await expect(charts.getByRole("article").first()).toBeVisible();

  // An infeasible chart gets flagged and blocks approval until resolved
  await say(page, "Show a 3D globe of failures");
  const decision = page.getByRole("article", { name: /Decision needed/ });
  await expect(decision).toContainText("can't draw");
  const approve = page.getByRole("button", { name: "Approve plan" });
  await expect(approve).toBeDisabled();
  await decision.getByRole("button", { name: "Drop" }).click();
  await expect(decision).toHaveCount(0);

  // Approve → build → dashboard renders
  await approve.click();
  await page.getByRole("button", { name: "Build dashboard" }).click();
  const canvas = page.getByLabel("Dashboard canvas");
  await expect(canvas.getByRole("img").first()).toBeVisible({ timeout: 30_000 });
  const version = page.getByTestId("dashboard-version");
  await expect(version).toHaveText("v1");

  // Edit via chat → preview → apply
  await say(page, "rename the pass rate by build to Build health");
  await page.getByRole("region", { name: "Proposed edit" }).getByRole("button", { name: "Apply" }).click();
  await expect(version).toHaveText("v2");
  await expect(canvas.getByRole("article", { name: "Build health" })).toBeVisible();

  // Undo via chat
  await say(page, "undo");
  await expect(version).toHaveText("v3");
  await expect(canvas.getByRole("article", { name: "Pass rate by build" })).toBeVisible();

  // Reopen from the library
  await page.getByRole("link", { name: "Library" }).click();
  await page.getByRole("list", { name: "Dashboards" }).getByRole("link", { name: /^Open / }).first().click();
  await expect(page.getByTestId("dashboard-version")).toHaveText("v3");
  await expect(page.getByLabel("Dashboard canvas").getByRole("article", { name: "Pass rate by build" })).toBeVisible();
});
