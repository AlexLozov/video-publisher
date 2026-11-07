import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import App from "../App";
import type { Settings } from "../api";
import i18n from "../i18n";
import { calls, mockFetch, type Route } from "../test/fetchMock";
import { HEALTH_OK, makeSettings, PLATFORMS, VALID_TOKEN } from "../test/fixtures";

function setup(settings: Settings = makeSettings(), extra: Record<string, Route> = {}) {
  let current = settings;
  const fetch = mockFetch({
    "/api/health": HEALTH_OK,
    "/api/platforms": PLATFORMS,
    "GET /api/settings": () => ({ body: current }),
    "PATCH /api/settings": ({ body }) => {
      const patch = body as Partial<Settings>;
      current = {
        ...current,
        ...patch,
        telegram: { ...current.telegram, ...(patch.telegram ?? {}) },
      };
      return { body: current };
    },
    "PUT /api/secrets/telegram_bot_token": { status: 204 },
    "DELETE /api/secrets/telegram_bot_token": { status: 204 },
    "PUT /api/secrets/telegram_api_hash": { status: 204 },
    "GET /api/telegram/account": () => ({
      body: { state: current.secrets.telegram_session ? "logged_in" : "logged_out", phone: null },
    }),
    ...extra,
  });
  render(<App />);
  return fetch;
}

async function openSettings() {
  await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
  // Network settings are an accordion: open the Telegram row.
  await userEvent.click(await screen.findByRole("button", { name: /^Telegram/ }));
}

const fieldError = (code: string, fields: Record<string, string>) => ({
  status: 422,
  body: { code, params: { fields } },
});

describe("settings page: networks accordion", () => {
  it("keeps networks collapsed until one is clicked and shows their state", async () => {
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
    const tg = await screen.findByRole("button", { name: /^Telegram/ });
    expect(tg).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("radio", { name: /Бот/ })).not.toBeInTheDocument();
    expect(screen.getByTestId("state-telegram")).toHaveTextContent("Не настроено");
    expect(screen.getByRole("button", { name: /^TikTok/ })).toBeDisabled();
    expect(screen.getByTestId("state-tiktok")).toHaveTextContent("Скоро");

    await userEvent.click(tg);
    expect(tg).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("radio", { name: /Бот/ })).toBeInTheDocument();

    await userEvent.click(tg);
    expect(screen.queryByRole("radio", { name: /Бот/ })).not.toBeInTheDocument();
  });

  it("marks Telegram as set up when the bot has a channel and a token", async () => {
    setup(
      makeSettings({
        telegram: { mode: "bot", channel: "@c", api_id: null },
        secrets: { telegram_bot_token: true, telegram_api_hash: false, telegram_session: false },
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
    expect(await screen.findByTestId("state-telegram")).toHaveTextContent("Настроено");
  });
});

describe("settings page: loading", () => {
  it("shows a loading state until settings arrive", async () => {
    mockFetch({ "/api/health": HEALTH_OK, "/api/platforms": PLATFORMS });
    // Never-resolving settings request
    const pending = new Promise<Response>(() => {});
    const original = globalThis.fetch;
    globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
      String(input) === "/api/settings" ? pending : original(input, init)) as typeof fetch;
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
    expect(screen.getByText("Загрузка…")).toBeInTheDocument();
  });

  it("shows a translated error when the server is unreachable", async () => {
    setup(makeSettings(), { "GET /api/settings": { error: new TypeError("Failed to fetch") } });
    await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось загрузить настройки: Нет связи с сервером",
    );
  });

  it("shows the backend error code when secrets storage is unavailable", async () => {
    setup(makeSettings(), {
      "GET /api/settings": {
        status: 503,
        body: { code: "secret_store_unavailable", params: {} },
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Настройки" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Хранилище секретов Windows недоступно",
    );
  });
});

describe("settings page: telegram mode", () => {
  it("defaults to bot mode with the bot token field", async () => {
    setup();
    await openSettings();
    expect(screen.getByRole("radio", { name: /Бот/ })).toBeChecked();
    expect(screen.getByText(/компьютер должен быть включён/)).toBeInTheDocument();
    expect(screen.getByLabelText("Токен бота")).toBeInTheDocument();
    expect(screen.getByTestId("telegram_bot_token-status")).toHaveTextContent("не задан");
    expect(screen.queryByLabelText("api_id")).not.toBeInTheDocument();
  });

  it("shows account fields after switching to account mode", async () => {
    setup();
    await openSettings();
    await userEvent.click(screen.getByRole("radio", { name: /Мой аккаунт/ }));
    expect(screen.getByLabelText("api_id")).toBeInTheDocument();
    expect(screen.getByLabelText("api_hash")).toBeInTheDocument();
    expect(await screen.findByTestId("telegram-login-status")).toHaveTextContent(
      "Вход не выполнен",
    );
    expect(screen.queryByLabelText("Токен бота")).not.toBeInTheDocument();
  });

  it("shows an active session", async () => {
    setup(
      makeSettings({
        telegram: { mode: "account", channel: null, api_id: 1 },
        secrets: { telegram_bot_token: false, telegram_api_hash: true, telegram_session: true },
      }),
    );
    await openSettings();
    expect(await screen.findByTestId("telegram-login-status")).toHaveTextContent("Вход выполнен");
    expect(screen.getByRole("button", { name: "Выйти" })).toBeInTheDocument();
    expect(screen.getByTestId("telegram_api_hash-status")).toHaveTextContent("задан");
  });

  it("saves mode, channel and api_id", async () => {
    const fetch = setup();
    await openSettings();
    await userEvent.click(screen.getByRole("radio", { name: /Мой аккаунт/ }));
    await userEvent.type(screen.getByLabelText("Канал"), "  @my_channel ");
    await userEvent.type(screen.getByLabelText("api_id"), "12345");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));

    expect(await screen.findByText("Сохранено")).toBeInTheDocument();
    expect(calls(fetch)).toContainEqual({
      method: "PATCH",
      url: "/api/settings",
      body: { telegram: { mode: "account", channel: "@my_channel", api_id: 12345 } },
    });
  });

  it("sends null for empty channel and api_id", async () => {
    const fetch = setup(makeSettings({ telegram: { mode: "bot", channel: "@old_one", api_id: 5 } }));
    await openSettings();
    await userEvent.clear(screen.getByLabelText("Канал"));
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await screen.findByText("Сохранено");
    expect(calls(fetch).at(-1)?.body).toEqual({
      telegram: { mode: "bot", channel: null, api_id: 5 },
    });
  });

  it("shows a field error for an invalid channel", async () => {
    setup(makeSettings(), {
      "PATCH /api/settings": fieldError("validation_error", {
        "body.telegram.channel": "invalid_channel",
      }),
    });
    await openSettings();
    await userEvent.type(screen.getByLabelText("Канал"), "my channel");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByText("Укажите @имя_канала или id вида -100…")).toBeInTheDocument();
    expect(screen.getByLabelText("Канал")).toHaveAttribute("aria-invalid", "true");
    expect(screen.queryByText("Сохранено")).not.toBeInTheDocument();
  });

  it("sends a non-numeric api_id as text so the backend can report it", async () => {
    const fetch = setup(makeSettings(), {
      "PATCH /api/settings": fieldError("validation_error", {
        "body.telegram.api_id": "int_parsing",
      }),
    });
    await openSettings();
    await userEvent.click(screen.getByRole("radio", { name: /Мой аккаунт/ }));
    await userEvent.type(screen.getByLabelText("api_id"), "12a");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByText("Введите целое число")).toBeInTheDocument();
    expect(calls(fetch).at(-1)?.body).toEqual({
      telegram: { mode: "account", channel: null, api_id: "12a" },
    });
  });

  it("falls back to a generic message for an unknown field error code", async () => {
    setup(makeSettings(), {
      "PATCH /api/settings": fieldError("validation_error", { "body.telegram.channel": "weird" }),
    });
    await openSettings();
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByText("Неверное значение")).toBeInTheDocument();
  });

  it("shows a general error when saving fails", async () => {
    setup(makeSettings(), {
      "PATCH /api/settings": { status: 500, body: { code: "internal_error", params: {} } },
    });
    await openSettings();
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Внутренняя ошибка сервера");
  });

  it("shows a network error when saving while offline", async () => {
    setup(makeSettings(), { "PATCH /api/settings": { error: new TypeError("offline") } });
    await openSettings();
    await userEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с сервером");
  });
});

describe("settings page: secrets", () => {
  it("saves the bot token and never shows it back", async () => {
    const fetch = setup();
    await openSettings();
    const input = screen.getByLabelText("Токен бота");
    expect(input).toHaveAttribute("type", "password");
    const save = screen.getByRole("button", { name: "Сохранить: Токен бота" });
    expect(save).toBeDisabled();

    await userEvent.type(input, VALID_TOKEN);
    await userEvent.click(save);

    await waitFor(() =>
      expect(screen.getByTestId("telegram_bot_token-status")).toHaveTextContent("задан"),
    );
    expect(input).toHaveValue("");
    expect(calls(fetch)).toContainEqual({
      method: "PUT",
      url: "/api/secrets/telegram_bot_token",
      body: { value: VALID_TOKEN },
    });
    expect(document.body.textContent).not.toContain(VALID_TOKEN);
  });

  it("shows a translated error for an invalid token", async () => {
    setup(makeSettings(), {
      "PUT /api/secrets/telegram_bot_token": {
        status: 422,
        body: { code: "invalid_bot_token", params: {} },
      },
    });
    await openSettings();
    await userEvent.type(screen.getByLabelText("Токен бота"), "garbage");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить: Токен бота" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Это не похоже на токен бота");
    expect(screen.getByTestId("telegram_bot_token-status")).toHaveTextContent("не задан");
    expect(screen.getByLabelText("Токен бота")).toHaveValue("garbage");
  });

  it("shows an error when Windows secret storage is unavailable", async () => {
    setup(makeSettings(), {
      "PUT /api/secrets/telegram_bot_token": {
        status: 503,
        body: { code: "secret_store_unavailable", params: {} },
      },
    });
    await openSettings();
    await userEvent.type(screen.getByLabelText("Токен бота"), VALID_TOKEN);
    await userEvent.click(screen.getByRole("button", { name: "Сохранить: Токен бота" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Хранилище секретов Windows недоступно",
    );
  });

  it("deletes a stored token", async () => {
    const fetch = setup(
      makeSettings({
        secrets: { telegram_bot_token: true, telegram_api_hash: false, telegram_session: false },
      }),
    );
    await openSettings();
    expect(screen.getByTestId("telegram_bot_token-status")).toHaveTextContent("задан");
    await userEvent.click(screen.getByRole("button", { name: "Удалить: Токен бота" }));
    await waitFor(() =>
      expect(screen.getByTestId("telegram_bot_token-status")).toHaveTextContent("не задан"),
    );
    expect(screen.queryByRole("button", { name: "Удалить: Токен бота" })).not.toBeInTheDocument();
    expect(calls(fetch)).toContainEqual({
      method: "DELETE",
      url: "/api/secrets/telegram_bot_token",
      body: undefined,
    });
  });

  it("saves the api_hash in account mode", async () => {
    setup(makeSettings({ telegram: { mode: "account", channel: null, api_id: null } }));
    await openSettings();
    await userEvent.type(screen.getByLabelText("api_hash"), "0123456789abcdef0123456789abcdef");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить: api_hash" }));
    await waitFor(() =>
      expect(screen.getByTestId("telegram_api_hash-status")).toHaveTextContent("задан"),
    );
  });
});

describe("settings page: language", () => {
  it("changes the interface language from the dropdown", async () => {
    const fetch = setup();
    await openSettings();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Язык интерфейса" }), "en");
    expect(await screen.findByRole("heading", { name: "General" })).toBeInTheDocument();
    expect(i18n.language).toBe("en");
    await waitFor(() =>
      expect(calls(fetch)).toContainEqual({
        method: "PATCH",
        url: "/api/settings",
        body: { language: "en" },
      }),
    );
  });
});
