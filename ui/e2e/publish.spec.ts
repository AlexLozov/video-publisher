import { expect, test } from "@playwright/test";

import { configureBot, FAKE_TELEGRAM_URL, makeVideo, resetBackend } from "./helpers";

// Error cases (format, size, missing file, Telegram errors) are covered by backend tests
// and Vitest; this is the end-to-end happy path through the real backend.

test.beforeEach(async ({ request }) => {
  await resetBackend(request);
});

test("publish: MP4 goes out through the bot and the result persists", async ({
  page,
  request,
}) => {
  await configureBot(request);
  const video = makeVideo("first.mp4");

  await page.goto("/");
  await page.getByRole("navigation").getByRole("button", { name: "Публикация" }).click();
  await expect(page.getByTestId("destination")).toHaveText("Telegram · Бот · @my_test_channel");

  await page.getByLabel("Путь к видеофайлу").fill(video);
  await page.getByLabel("Заголовок").fill("Первый ролик");
  await page.getByLabel("Текст").fill("Описание");
  await page.getByLabel("Хэштеги").fill("#test, e2e");
  await page.getByRole("button", { name: "Опубликовать сейчас" }).click();

  const item = page
    .getByTestId("history-card")
    .filter({ hasText: "Первый ролик" })
    .getByTestId("publication");
  await expect(item.getByTestId("publication-status")).toHaveText("опубликовано");
  await expect(item.getByRole("link", { name: "Открыть пост" })).toHaveAttribute(
    "href",
    /^https:\/\/t\.me\/my_test_channel\/\d+$/,
  );

  const calls = await (await request.get(`${FAKE_TELEGRAM_URL}/calls`)).json();
  expect(calls).toEqual([
    {
      method: "sendVideo",
      chat_id: "@my_test_channel",
      caption: "Первый ролик\n\nОписание\n\n#test #e2e",
      filename: "first.mp4",
    },
  ]);

  await page.reload();
  await page.getByRole("navigation").getByRole("button", { name: "Публикация" }).click();
  await expect(
    page.getByTestId("history-card").filter({ hasText: "Первый ролик" }).getByTestId(
      "publication-status",
    ),
  ).toHaveText("опубликовано");
});
