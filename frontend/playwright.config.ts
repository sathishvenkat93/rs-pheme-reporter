import { defineConfig, devices } from "@playwright/test";

const api = Number(process.env.E2E_API_PORT ?? 8010);
const web = Number(process.env.E2E_WEB_PORT ?? 5180);

// Started by scripts/e2e.sh, which isolates the database, queue and bucket and runs the worker.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://localhost:${web}`, trace: "retain-on-failure", viewport: { width: 1280, height: 860 } },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 860 } } }],
  webServer: [
    {
      command: `cd ../backend && uv run uvicorn app.main:app --port ${api}`,
      url: `http://localhost:${api}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `VITE_PORT=${web} VITE_API_TARGET=http://localhost:${api} pnpm exec vite --strictPort`,
      url: `http://localhost:${web}`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
