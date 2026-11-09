import { expect, test } from "@playwright/test";

import { resetBackend } from "./helpers";

// One key scenario per screen (docs/rules.md). Translations are covered by the locale
// key-parity unit tests, so E2E runs in Russian only.

test.beforeEach(async ({ request }) => {
  await resetBackend(request);
});

test("start screen: backend status and platforms", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("html")).toHaveAttribute("lang", "ru");
  await expect(page.getByRole("heading", { name: "Video Publisher" })).toBeVisible();
  await expect(page.getByText(/Сервер работает · v\d+\.\d+\.\d+/)).toBeVisible();
  for (const name of ["YouTube / Shorts", "TikTok", "Instagram", "Страница Facebook", "Telegram"]) {
    await expect(page.getByText(name, { exact: true })).toBeVisible();
  }
  // Telegram, YouTube and Facebook can publish; the other platforms are still in development.
  await expect(page.getByText("в разработке", { exact: true })).toHaveCount(2);
  await expect(page.getByText("подключено", { exact: true })).toHaveCount(3);
});
