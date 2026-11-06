import os from "node:os";
import path from "node:path";

import { defineConfig, devices } from "@playwright/test";

// E2E tests run against the real backend serving the built UI (`npm run build` first).
const PORT = Number(process.env.VP_E2E_PORT ?? 8790);
const FAKE_TELEGRAM_PORT = PORT + 1;
const FAKE_TELEGRAM_URL = `http://127.0.0.1:${FAKE_TELEGRAM_PORT}`;
const repoRoot = path.resolve(import.meta.dirname, "..");
const python =
  process.env.VP_PYTHON ??
  path.join(repoRoot, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
// Fresh database per run; secrets stay in memory so the real Windows Credential Manager is
// never touched.
const dataDir = path.join(os.tmpdir(), `video-publisher-e2e-${process.pid}-${Date.now()}`);

export default defineConfig({
  testDir: "./e2e",
  // Tests share one backend (one database), so they run one at a time and reset state first.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      // Fake Telegram Bot API: E2E tests never talk to the real Telegram.
      command: `"${python}" tests/fake_telegram_server.py ${FAKE_TELEGRAM_PORT}`,
      cwd: repoRoot,
      url: `http://127.0.0.1:${FAKE_TELEGRAM_PORT}/calls`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `"${python}" -m uvicorn video_publisher.api.app:create_app_from_settings --factory --host 127.0.0.1 --port ${PORT}`,
      cwd: repoRoot,
      env: {
        VP_UI_DIST_DIR: path.join(repoRoot, "ui", "dist"),
        VP_DATA_DIR: dataDir,
        VP_SECRET_BACKEND: "memory",
        VP_TELEGRAM_API_BASE: FAKE_TELEGRAM_URL,
      },
      url: `http://127.0.0.1:${PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
