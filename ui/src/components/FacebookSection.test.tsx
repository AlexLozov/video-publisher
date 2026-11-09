import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { FacebookLoginStatus, Settings } from "../api";
import { calls, mockFetch, type Route } from "../test/fetchMock";
import { makeSettings } from "../test/fixtures";
import { FacebookSection } from "./FacebookSection";

const REDIRECT = "http://localhost:47615/facebook/callback";

function st(overrides: Partial<FacebookLoginStatus> = {}): FacebookLoginStatus {
  return { state: "logged_out", page: null, pages: [], redirect_uri: REDIRECT, error: null, ...overrides };
}

const KEYS = makeSettings({
  facebook: { app_id: "123456789012", page_id: null, page_name: null },
  secrets: { facebook_app_secret: true },
});

function setup(settings: Settings, routes: Record<string, Route>) {
  const fetch = mockFetch(routes);
  const onSaved = vi.fn();
  const onSecret = vi.fn();
  render(<FacebookSection settings={settings} onSaved={onSaved} onSecret={onSecret} pollMs={10} />);
  return { fetch, onSaved, onSecret };
}

describe("facebook settings", () => {
  it("asks for the Meta app keys before login and shows the redirect address", async () => {
    setup(makeSettings(), { "GET /api/facebook/account": { body: st() } });
    expect(
      await screen.findByText("Сначала сохраните App ID и App Secret — без них войти нельзя."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Войти через Facebook" })).not.toBeInTheDocument();
    expect(screen.getByTestId("fb-redirect")).toHaveTextContent(REDIRECT);
  });

  it("logs in through the browser, waits, then lets the user pick a page", async () => {
    let status = st();
    const { fetch, onSaved } = setup(KEYS, {
      "GET /api/facebook/account": () => ({ body: status }),
      "POST /api/facebook/account/login": () => {
        status = st({ state: "waiting" });
        return { body: status };
      },
      "POST /api/facebook/account/select-page": ({ body }) => {
        const id = (body as { page_id: string }).page_id;
        status = st({ state: "logged_in", page: { id, name: "Page B" } });
        return { body: status };
      },
    });

    await userEvent.click(await screen.findByRole("button", { name: "Войти через Facebook" }));
    expect(await screen.findByText(/Открылся браузер/)).toBeInTheDocument();

    // The browser part finishes on the backend; polling picks it up.
    status = st({
      state: "choose_page",
      pages: [
        { id: "1", name: "Page A" },
        { id: "2", name: "Page B" },
      ],
    });
    await userEvent.click(await screen.findByRole("button", { name: "Page B" }));

    expect(await screen.findByText("Вход выполнен. Страница: Page B")).toBeInTheDocument();
    expect(calls(fetch).find((c) => c.url.endsWith("/select-page"))?.body).toEqual({ page_id: "2" });
    const saved = onSaved.mock.calls.at(-1)?.[0] as Settings;
    expect(saved.facebook.page_id).toBe("2");
    expect(saved.secrets.facebook_page_token).toBe(true);
  });

  it("shows why the last login failed", async () => {
    setup(KEYS, {
      "GET /api/facebook/account": { body: st({ error: "facebook_login_denied" }) },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Вход через Facebook отменён или доступ не разрешён.",
    );
  });

  it("logs out", async () => {
    const settings = makeSettings({
      facebook: { app_id: "123456789012", page_id: "1", page_name: "Page A" },
      secrets: { facebook_app_secret: true, facebook_page_token: true },
    });
    const { onSaved } = setup(settings, {
      "GET /api/facebook/account": {
        body: st({ state: "logged_in", page: { id: "1", name: "Page A" } }),
      },
      "POST /api/facebook/account/logout": { body: st() },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Выйти" }));
    await waitFor(() =>
      expect((onSaved.mock.calls.at(-1)?.[0] as Settings).secrets.facebook_page_token).toBe(false),
    );
    expect(await screen.findByRole("button", { name: "Войти через Facebook" })).toBeInTheDocument();
  });

  it("shows a field error for a wrong App ID", async () => {
    setup(makeSettings(), {
      "GET /api/facebook/account": { body: st() },
      "PATCH /api/settings": {
        status: 422,
        body: {
          code: "validation_error",
          params: { fields: { "body.facebook.app_id": "invalid_app_id" } },
        },
      },
    });
    await userEvent.type(await screen.findByLabelText("App ID"), "abc");
    await userEvent.click(screen.getAllByRole("button", { name: "Сохранить" })[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent("App ID — это число из 5–20 цифр");
  });
});
