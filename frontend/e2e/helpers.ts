import path from "node:path";
import { expect, type Page } from "@playwright/test";

export const FIXTURES = path.resolve(import.meta.dirname, "../../backend/tests/fixtures");
export const JUNIT = [101, 102, 103, 104].map((b) => path.join(FIXTURES, `junit-build-${b}.xml`));

/** New session → upload the JUnit builds → wait for the proposal. */
export async function startWithJunit(page: Page, files = JUNIT): Promise<string> {
  await page.goto("/");
  await page.waitForURL(/\/s\//);
  await page.getByTestId("file-input").setInputFiles(files);
  await expect(page.getByRole("list", { name: "Uploads" }).getByText("ready")).toHaveCount(files.length, { timeout: 30_000 });
  await expect(page.getByRole("region", { name: "Charts" })).toBeVisible({ timeout: 30_000 });
  return page.url().split("/s/")[1];
}

export async function say(page: Page, text: string): Promise<void> {
  await page.getByLabel("Message Pheme").fill(text);
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByLabel("Pheme is replying")).toHaveCount(0, { timeout: 30_000 });
}
