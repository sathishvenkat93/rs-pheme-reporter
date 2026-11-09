// The last step of the Phase 3 flow in the browser: upload a second file to refresh a live dashboard.
import path from "node:path";
import { expect, test } from "@playwright/test";
import { FIXTURES, startWithJunit } from "./helpers";

test("upload new data refreshes the live dashboard", async ({ page }) => {
  await startWithJunit(page);
  await page.getByRole("button", { name: "Approve plan" }).click();
  await page.getByRole("button", { name: "Build dashboard" }).click();
  const canvas = page.getByLabel("Dashboard canvas");
  await expect(canvas).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Upload new data" }).click();
  await page.getByTestId("file-input").last().setInputFiles(path.join(FIXTURES, "junit-build-105.xml"));
  await expect(page.getByRole("status").filter({ hasText: "now includes the new data" })).toBeVisible({ timeout: 30_000 });
  // The pass-rate-by-build trend now has a fifth build on its axis.
  await expect(canvas.getByRole("img", { name: /Pass rate by build/ }).getByText("105")).toBeVisible();

  await page.getByTestId("file-input").last().setInputFiles(path.join(FIXTURES, "jmeter-load.jtl"));
  await expect(page.getByRole("table").filter({ hasText: "Dashboard field" })).toBeVisible({ timeout: 30_000 });
});
