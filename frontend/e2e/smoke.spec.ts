// Phase 4 deliverable: upload → options appear → approve → dashboard renders.
import { expect, test } from "@playwright/test";
import { startWithJunit } from "./helpers";

test("smoke: upload, approve, build, dashboard renders", async ({ page }) => {
  await startWithJunit(page);
  await expect(page.getByRole("region", { name: "Charts" }).getByRole("article").first()).toBeVisible();
  await page.getByRole("button", { name: "Approve plan" }).click();
  await expect(page.getByTestId("session-state")).toHaveText("approved");
  await page.getByRole("button", { name: "Build dashboard" }).click();
  const canvas = page.getByLabel("Dashboard canvas");
  await expect(canvas).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("dashboard-version")).toHaveText("v1");
  const widgets = canvas.getByRole("article");
  expect(await widgets.count()).toBeGreaterThanOrEqual(6);
  await expect(canvas.getByRole("img").first()).toBeVisible(); // charts drew
  await expect(canvas.getByRole("alert")).toHaveCount(0); // no widget errors
});
