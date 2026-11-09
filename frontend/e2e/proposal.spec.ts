// M10 "Done when": the JUnit flow works in the browser up to approval, including resolving a flag.
import { expect, test } from "@playwright/test";
import { say, startWithJunit } from "./helpers";

test("upload → options → flagged request → resolve → approve", async ({ page }) => {
  await startWithJunit(page);
  const charts = page.getByRole("region", { name: "Charts" });
  await expect(charts.getByRole("article").first()).toBeVisible();
  expect(await charts.getByRole("article").count()).toBeGreaterThanOrEqual(6);
  await expect(charts.getByRole("img").first()).toBeVisible(); // live preview thumbnails

  await say(page, "Show a pie of failures by test");
  const card = page.getByRole("article", { name: /Decision needed/ });
  await expect(card).toBeVisible();
  const approve = page.getByRole("button", { name: "Approve plan" });
  await expect(approve).toBeDisabled();
  await expect(page.getByText(/Plan updated:/).first()).toBeVisible();

  await card.getByRole("button", { name: "Accept alternative" }).click();
  await expect(card).toHaveCount(0);
  await expect(approve).toBeEnabled();
  await approve.click();
  await expect(page.getByTestId("session-state")).toHaveText("approved");
});

test("every action is reachable by keyboard", async ({ page }) => {
  await startWithJunit(page);
  await page.getByRole("button", { name: /Upload files/ }).focus();
  await page.keyboard.press("Tab");
  // The context box, the chat input and the cards' checkboxes are all in the tab order.
  const focusable = await page.$$eval("button, input, summary, [tabindex='0']", (els) =>
    els.filter((e) => !(e as HTMLInputElement).disabled && (e as HTMLElement).tabIndex >= 0).length);
  expect(focusable).toBeGreaterThan(10);
  const unnamed = await page.$$eval("button, input[type=checkbox]", (els) =>
    els.filter((e) => !(e.getAttribute("aria-label") || e.textContent?.trim() || e.closest("label"))).length);
  expect(unnamed).toBe(0);
});
