import { expect, test, type Page } from "@playwright/test";

const SCREENS = {
  ru: {
    status: /Сервер работает · v\d+\.\d+\.\d+/,
    subtitle: "Публикация одного видео сразу в несколько соцсетей",
    platforms: ["YouTube / Shorts", "TikTok", "Instagram", "Страница Facebook", "Telegram"],
    badge: "в разработке",
  },
  en: {
    status: /Server is running · v\d+\.\d+\.\d+/,
    subtitle: "Publish one video to several social networks at once",
    platforms: ["YouTube / Shorts", "TikTok", "Instagram", "Facebook Page", "Telegram"],
    badge: "in development",
  },
} as const;

async function expectScreen(page: Page, lng: keyof typeof SCREENS) {
  const s = SCREENS[lng];
  await expect(page.locator("html")).toHaveAttribute("lang", lng);
  await expect(page.getByRole("heading", { name: "Video Publisher" })).toBeVisible();
  await expect(page.getByText(s.subtitle)).toBeVisible();
  await expect(page.getByText(s.status)).toBeVisible();
  for (const name of s.platforms) {
    await expect(page.getByText(name, { exact: true })).toBeVisible();
  }
  await expect(page.getByText(s.badge, { exact: true })).toHaveCount(5);
  await expect(page.getByRole("button", { name: lng.toUpperCase() })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
}

test("opens in Russian by default with backend status and all platforms", async ({ page }) => {
  await page.goto("/");
  await expectScreen(page, "ru");
});

test("switches to English and keeps it after reload", async ({ page }) => {
  await page.goto("/");
  await expectScreen(page, "ru");

  await page.getByRole("button", { name: "EN" }).click();
  await expectScreen(page, "en");

  await page.reload();
  await expectScreen(page, "en");
});

test("switches back from English to Russian", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "EN" }).click();
  await expectScreen(page, "en");

  await page.getByRole("button", { name: "RU" }).click();
  await expectScreen(page, "ru");

  await page.reload();
  await expectScreen(page, "ru");
});

test("shows an error state when the backend fails", async ({ page }) => {
  await page.route("**/api/health", (route) =>
    route.fulfill({ status: 500, json: { code: "internal_error", params: {} } }),
  );
  await page.goto("/");
  await expect(page.getByText("Сервер недоступен")).toBeVisible();
  await page.getByRole("button", { name: "EN" }).click();
  await expect(page.getByText("Server is unavailable")).toBeVisible();
});
