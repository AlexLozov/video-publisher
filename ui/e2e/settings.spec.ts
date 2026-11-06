import { expect, test, type Page } from "@playwright/test";

import { resetBackend } from "./helpers";

const VALID_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawQ";
const VALID_HASH = "0123456789abcdef0123456789abcdef";

test.beforeEach(async ({ request }) => {
  await resetBackend(request);
});

async function openSettings(page: Page, label = "Настройки") {
  await page.getByRole("navigation").getByRole("button", { name: label }).click();
  await expect(page.getByRole("heading", { name: "Telegram" })).toBeVisible();
}

test("bot mode is the default and shows the bot token field", async ({ page }) => {
  await page.goto("/");
  await openSettings(page);
  await expect(page.getByRole("radio", { name: /Бот/ })).toBeChecked();
  await expect(page.getByLabel("Токен бота", { exact: true })).toBeVisible();
  await expect(page.getByTestId("telegram_bot_token-status")).toHaveText("не задан");
});

test("account mode, channel and api_id are saved and survive a reload", async ({ page }) => {
  await page.goto("/");
  await openSettings(page);

  await page.getByRole("radio", { name: /Мой аккаунт/ }).check();
  await page.getByLabel("Канал").fill("@my_test_channel");
  await page.getByLabel("api_id").fill("1234567");
  await page.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect(page.getByText("Сохранено")).toBeVisible();

  await page.reload();
  await openSettings(page);
  await expect(page.getByRole("radio", { name: /Мой аккаунт/ })).toBeChecked();
  await expect(page.getByLabel("Канал")).toHaveValue("@my_test_channel");
  await expect(page.getByLabel("api_id")).toHaveValue("1234567");
});

test("invalid channel and api_id show field errors and are not saved", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await openSettings(page);
  await page.getByLabel("Канал").fill("not a channel");
  await page.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect(page.getByText("Укажите @имя_канала или id вида -100…")).toBeVisible();

  await page.getByLabel("Канал").fill("");
  await page.getByRole("radio", { name: /Мой аккаунт/ }).check();
  await page.getByLabel("api_id").fill("abc");
  await page.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect(page.getByText("Введите целое число")).toBeVisible();

  const settings = await (await request.get("/api/settings")).json();
  expect(settings.telegram).toEqual({ mode: "bot", channel: null, api_id: null });
});

test("bot token: rejects garbage, stores a valid token write-only, deletes it", async ({
  page,
}) => {
  await page.goto("/");
  await openSettings(page);

  await page.getByLabel("Токен бота", { exact: true }).fill("garbage");
  await page.getByRole("button", { name: "Сохранить: Токен бота" }).click();
  await expect(page.getByRole("alert")).toContainText("Это не похоже на токен бота");
  await expect(page.getByTestId("telegram_bot_token-status")).toHaveText("не задан");

  await page.getByLabel("Токен бота", { exact: true }).fill(VALID_TOKEN);
  await page.getByRole("button", { name: "Сохранить: Токен бота" }).click();
  await expect(page.getByTestId("telegram_bot_token-status")).toHaveText("задан");
  await expect(page.getByLabel("Токен бота", { exact: true })).toHaveValue("");

  await page.reload();
  await openSettings(page);
  await expect(page.getByTestId("telegram_bot_token-status")).toHaveText("задан");
  expect(await page.content()).not.toContain(VALID_TOKEN);

  await page.getByRole("button", { name: "Удалить: Токен бота" }).click();
  await expect(page.getByTestId("telegram_bot_token-status")).toHaveText("не задан");
});

test("api_hash is stored in account mode", async ({ page }) => {
  await page.goto("/");
  await openSettings(page);
  await page.getByRole("radio", { name: /Мой аккаунт/ }).check();
  await page.getByLabel("api_hash", { exact: true }).fill(VALID_HASH);
  await page.getByRole("button", { name: "Сохранить: api_hash" }).click();
  await expect(page.getByTestId("telegram_api_hash-status")).toHaveText("задан");
  await expect(page.getByTestId("telegram-session-status")).toContainText("не выполнен");
});

test("language chosen in settings is stored on the backend", async ({ page, context }) => {
  await page.goto("/");
  await openSettings(page);
  await page.getByRole("combobox", { name: "Язык интерфейса" }).selectOption("en");
  await expect(page.getByRole("heading", { name: "General" })).toBeVisible();

  // Wipe the browser-side cache: the language must still come back from the backend.
  await context.clearCookies();
  await page.evaluate(() => window.localStorage.clear());
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.getByText(/Server is running/)).toBeVisible();
});

test("settings screen is fully translated to English", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "EN" }).click();
  await openSettings(page, "Settings");
  await page.getByRole("radio", { name: /My account/ }).check();
  await expect(page.getByText(/Telegram stores scheduled posts/)).toBeVisible();
  await expect(page.getByLabel("api_hash", { exact: true })).toBeVisible();
  const text = await page.locator("main").evaluate((el) => {
    const clone = el.cloneNode(true) as HTMLElement;
    clone.querySelectorAll("[lang]").forEach((n) => n.remove());
    return clone.textContent ?? "";
  });
  expect(text).not.toMatch(/[А-Яа-яЁё]/);
});
