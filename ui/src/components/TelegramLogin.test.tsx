import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { TelegramLoginStatus } from "../api";
import { calls, mockFetch, type Route } from "../test/fetchMock";
import { TelegramLogin } from "./TelegramLogin";

const BASE = "/api/telegram/account";
const status = (state: TelegramLoginStatus["state"], phone: string | null = null) => ({
  body: { state, phone },
});
const fail = (status: number, code: string, params: Record<string, unknown> = {}) => ({
  status,
  body: { code, params },
});

function setup(
  routes: Record<string, Route>,
  props: { keysReady?: boolean; sessionSet?: boolean } = {},
) {
  const fetch = mockFetch({ [`GET ${BASE}`]: status("logged_out"), ...routes });
  const onSessionChange = vi.fn();
  render(
    <TelegramLogin
      keysReady={props.keysReady ?? true}
      sessionSet={props.sessionSet ?? false}
      onSessionChange={onSessionChange}
    />,
  );
  return { fetch, onSessionChange };
}

describe("telegram account sign-in", () => {
  it("goes phone → code → cloud password → signed in", async () => {
    const { fetch, onSessionChange } = setup({
      [`POST ${BASE}/send-code`]: status("code_sent", "+7••••••4567"),
      [`POST ${BASE}/sign-in`]: status("password_needed", "+7••••••4567"),
      [`POST ${BASE}/password`]: status("logged_in"),
    });

    await userEvent.type(await screen.findByLabelText("Номер телефона"), " +79991234567 ");
    await userEvent.click(screen.getByRole("button", { name: "Получить код" }));
    expect(
      await screen.findByText("Код отправлен в Telegram на номер +7••••••4567."),
    ).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Код из Telegram"), "12345");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByText(/Включена двухэтапная проверка/)).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("Облачный пароль"), "secret pass");
    await userEvent.click(screen.getByRole("button", { name: "Подтвердить" }));

    expect(await screen.findByTestId("telegram-login-status")).toHaveTextContent("Вход выполнен");
    expect(screen.getByRole("button", { name: "Выйти" })).toBeInTheDocument();
    expect(onSessionChange).toHaveBeenCalledWith(true);
    const posts = calls(fetch).filter((c) => c.method === "POST");
    expect(posts).toEqual([
      { method: "POST", url: `${BASE}/send-code`, body: { phone: "+79991234567" } },
      { method: "POST", url: `${BASE}/sign-in`, body: { code: "12345" } },
      { method: "POST", url: `${BASE}/password`, body: { password: "secret pass" } },
    ]);
  });

  it("cannot start without api keys", async () => {
    setup({}, { keysReady: false });
    await userEvent.type(await screen.findByLabelText("Номер телефона"), "+79991234567");
    expect(screen.getByText(/Сначала сохраните api_id и api_hash/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Получить код" })).toBeDisabled();
  });

  it("shows how long Telegram asks to wait", async () => {
    setup({ [`POST ${BASE}/send-code`]: fail(429, "telegram_flood_wait", { seconds: 120 }) });
    await userEvent.type(await screen.findByLabelText("Номер телефона"), "+79991234567");
    await userEvent.click(screen.getByRole("button", { name: "Получить код" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Telegram просит подождать 120 с.",
    );
    expect(screen.getByLabelText("Номер телефона")).toBeInTheDocument();
  });

  it("keeps the code step after a wrong code", async () => {
    setup({
      [`GET ${BASE}`]: status("code_sent", "+7••••••4567"),
      [`POST ${BASE}/sign-in`]: fail(422, "telegram_code_invalid"),
    });
    await userEvent.type(await screen.findByLabelText("Код из Telegram"), "00000");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Неверный код.");
    expect(screen.getByLabelText("Код из Telegram")).toBeInTheDocument();
  });

  it("returns to the phone step when the code expired", async () => {
    let state: TelegramLoginStatus["state"] = "code_sent";
    setup({
      [`GET ${BASE}`]: () => status(state, "+7••••••4567"),
      [`POST ${BASE}/sign-in`]: () => {
        state = "logged_out";
        return fail(422, "telegram_code_expired");
      },
    });
    await userEvent.type(await screen.findByLabelText("Код из Telegram"), "12345");
    await userEvent.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Срок действия кода истёк.");
    expect(await screen.findByLabelText("Номер телефона")).toBeInTheDocument();
  });

  it("cancels a sign-in in progress", async () => {
    setup({
      [`GET ${BASE}`]: status("password_needed", "+7••••••4567"),
      [`POST ${BASE}/cancel`]: status("logged_out"),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Отмена" }));
    expect(await screen.findByLabelText("Номер телефона")).toBeInTheDocument();
  });

  it("signs out", async () => {
    const { onSessionChange } = setup(
      { [`GET ${BASE}`]: status("logged_in"), [`POST ${BASE}/logout`]: status("logged_out") },
      { sessionSet: true },
    );
    await userEvent.click(await screen.findByRole("button", { name: "Выйти" }));
    expect(await screen.findByTestId("telegram-login-status")).toHaveTextContent(
      "Вход не выполнен",
    );
    expect(onSessionChange).toHaveBeenCalledWith(false);
  });

  it("reports a network failure while loading", async () => {
    setup({ [`GET ${BASE}`]: { error: new TypeError("Failed to fetch") } }, { sessionSet: true });
    expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с сервером");
    await waitFor(() =>
      expect(screen.getByTestId("telegram-login-status")).toHaveTextContent("Вход выполнен"),
    );
  });
});
