import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

// E2E tests run against the real backend serving the built UI (`npm run build` first).
const PORT = Number(process.env.VP_E2E_PORT ?? 8790);
const repoRoot = path.resolve(import.meta.dirname, "..");
const python =
  process.env.VP_PYTHON ??
  path.join(repoRoot, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `"${python}" -m uvicorn video_publisher.api.app:create_app_from_settings --factory --host 127.0.0.1 --port ${PORT}`,
    cwd: repoRoot,
    env: { VP_UI_DIST_DIR: path.join(repoRoot, "ui", "dist") },
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
