import { expect, test } from "@playwright/test";

import { resetBackend, VALID_TOKEN } from "./helpers";

test.beforeEach(async ({ request }) => {
  await resetBackend(request);
});

test("settings: Telegram bot setup is saved and survives a reload", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation").getByRole("button", { name: "Настройки" }).click();
  await expect(page.getByRole("radio", { name: /Бот/ })).toBeChecked();

  await page.getByLabel("Канал").fill("@my_test_channel");
  await page.getByRole("button", { name: "Сохранить", exact: true }).click();
  await expect(page.getByText("Сохранено")).toBeVisible();

  await page.getByLabel("Токен бота", { exact: true }).fill(VALID_TOKEN);
  await page.getByRole("button", { name: "Сохранить: Токен бота" }).click();
  await expect(page.getByTestId("telegram_bot_token-status")).toHaveText("задан");

  await page.reload();
  await page.getByRole("navigation").getByRole("button", { name: "Настройки" }).click();
  await expect(page.getByLabel("Канал")).toHaveValue("@my_test_channel");
  await expect(page.getByTestId("telegram_bot_token-status")).toHaveText("задан");
  expect(await page.content()).not.toContain(VALID_TOKEN); // write-only secret
});
