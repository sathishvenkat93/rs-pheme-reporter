// M11 "Done when": build, inline rename, change a chart type via chat, undo from history,
// reopen from the library and see every version listed.
import { expect, test, type Page } from "@playwright/test";
import { say, startWithJunit } from "./helpers";

async function build(page: Page) {
  await startWithJunit(page);
  await page.getByRole("button", { name: "Approve plan" }).click();
  await page.getByRole("button", { name: "Build dashboard" }).click();
  await expect(page.getByLabel("Dashboard canvas")).toBeVisible({ timeout: 30_000 });
}

test("edit, undo, reopen from the library", async ({ page }) => {
  await build(page);
  const version = page.getByTestId("dashboard-version");
  const canvas = page.getByLabel("Dashboard canvas");

  // Inline rename (direct manipulation → PATCH with base_version)
  await canvas.getByRole("button", { name: "Rename Pass rate by build" }).click();
  const input = canvas.getByLabel("Rename Pass rate by build");
  await input.fill("Build health");
  await input.press("Enter");
  await expect(canvas.getByRole("article", { name: "Build health" })).toBeVisible();
  await expect(version).toHaveText("v2");

  // Select it, then change its chart type by chat → preview → Apply
  await canvas.getByRole("button", { name: "Select Build health" }).click();
  await expect(page.getByTestId("selection-chip")).toContainText("Editing:");
  await say(page, "make the build health a bar chart");
  const preview = page.getByRole("region", { name: "Proposed edit" });
  await expect(preview).toBeVisible();
  await preview.getByRole("button", { name: "Apply" }).click();
  await expect(version).toHaveText("v3");
  await expect(canvas.getByRole("img", { name: /Build health \(bar chart\)/ })).toBeVisible();

  // Undo from history: restore v2 (as v4)
  await page.getByRole("button", { name: "History" }).click();
  const drawer = page.getByRole("complementary", { name: "Version history" });
  await drawer.getByRole("listitem", { name: "Version 2" }).getByRole("button", { name: "Restore" }).click();
  await expect(version).toHaveText("v4");
  await expect(canvas.getByRole("img", { name: /Build health \(line chart\)/ })).toBeVisible();

  // Diff vs current from the drawer
  await drawer.getByRole("listitem", { name: "Version 1" }).getByRole("button", { name: "Diff vs current" }).click();
  await expect(drawer.getByRole("list", { name: "Changes from v1" })).toContainText("/title");

  // Reopen from the library: every version is listed
  await page.getByRole("link", { name: "Library" }).click();
  const list = page.getByRole("list", { name: "Dashboards" });
  await list.getByRole("link", { name: /^Open / }).first().click();
  await expect(page.getByLabel("Dashboard canvas")).toBeVisible();
  await page.getByRole("button", { name: "History" }).click();
  for (const n of [1, 2, 3, 4]) {
    await expect(page.getByRole("complementary", { name: "Version history" }).getByRole("listitem", { name: `Version ${n}` })).toBeVisible();
  }
});

test("drag a widget and view an old version read-only", async ({ page }) => {
  await build(page);
  const canvas = page.getByLabel("Dashboard canvas");
  const first = canvas.getByRole("article").first();
  const handle = first.locator(".drag-handle");
  const box = await handle.boundingBox();
  if (!box) throw new Error("no drag handle");
  await page.mouse.move(box.x + 4, box.y + 4);
  await page.mouse.down();
  await page.mouse.move(box.x + 400, box.y + 300, { steps: 12 });
  await page.mouse.up();
  await expect(page.getByTestId("dashboard-version")).toHaveText("v2");
  await page.getByRole("button", { name: "History" }).click();
  await expect(page.getByRole("complementary", { name: "Version history" }).getByText("Moved a widget")).toBeVisible();
  await page.getByRole("complementary", { name: "Version history" }).getByRole("listitem", { name: "Version 1" }).getByRole("link", { name: "View" }).click();
  await expect(page.getByText("Viewing version 1 (read-only).")).toBeVisible();
  await expect(page.getByTestId("dashboard-version")).toHaveText("v1");
  await expect(page.locator(".drag-handle")).toHaveCount(0);
});
