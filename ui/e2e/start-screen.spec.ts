import { expect, test } from "@playwright/test";

test("start screen shows backend status and all platforms", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Video Publisher" })).toBeVisible();
  await expect(page.getByText(/Сервер работает · v\d+\.\d+\.\d+/)).toBeVisible();

  for (const name of ["YouTube / Shorts", "TikTok", "Instagram", "Facebook Page", "Telegram"]) {
    await expect(page.getByText(name, { exact: true })).toBeVisible();
  }
});
