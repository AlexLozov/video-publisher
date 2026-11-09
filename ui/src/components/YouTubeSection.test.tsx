import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Settings, YouTubeLoginStatus } from "../api";
import { calls, mockFetch, type Route } from "../test/fetchMock";
import { makeSettings } from "../test/fixtures";
import { parseTags, YouTubeSection } from "./YouTubeSection";

const CLIENT_ID = "123456789012-abcdefghij0123456789.apps.googleusercontent.com";

function st(overrides: Partial<YouTubeLoginStatus> = {}): YouTubeLoginStatus {
  return { state: "logged_out", channel: null, error: null, ...overrides };
}

const KEYS = makeSettings({
  youtube: { ...makeSettings().youtube, client_id: CLIENT_ID },
  secrets: { youtube_client_secret: true },
});

function setup(settings: Settings, routes: Record<string, Route>) {
  const fetch = mockFetch(routes);
  const onSaved = vi.fn();
  const onSecret = vi.fn();
  render(<YouTubeSection settings={settings} onSaved={onSaved} onSecret={onSecret} pollMs={10} />);
  return { fetch, onSaved, onSecret };
}

describe("youtube settings", () => {
  it("asks for the Google client keys before login", async () => {
    setup(makeSettings(), { "POST /api/youtube/account/verify": { body: st() } });
    expect(
      await screen.findByText(
        "Сначала сохраните Client ID и Client Secret — без них войти нельзя.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Войти через Google" })).not.toBeInTheDocument();
    expect(screen.getByText(/остаются ограниченными/)).toBeInTheDocument();
  });

  it("logs in through the browser and reports the channel", async () => {
    let status = st();
    const { onSaved } = setup(KEYS, {
      "POST /api/youtube/account/verify": () => ({ body: status }),
      "GET /api/youtube/account": () => ({ body: status }),
      "POST /api/youtube/account/login": () => {
        status = st({ state: "waiting" });
        return { body: status };
      },
      "POST /api/youtube/account/logout": () => {
        status = st();
        return { body: status };
      },
    });

    await userEvent.click(await screen.findByRole("button", { name: "Войти через Google" }));
    expect(await screen.findByText(/Открылся браузер/)).toBeInTheDocument();

    // The browser part finishes on the backend; polling picks it up.
    status = st({ state: "logged_in", channel: { id: "UC1", title: "My Channel" } });
    expect(await screen.findByText("Вход выполнен. Канал: My Channel")).toBeInTheDocument();
    expect(onSaved).toHaveBeenLastCalledWith(
      expect.objectContaining({
        youtube: expect.objectContaining({ channel_id: "UC1", channel_title: "My Channel" }),
        secrets: expect.objectContaining({ youtube_refresh_token: true }),
      }),
    );

    await userEvent.click(screen.getByRole("button", { name: "Выйти" }));
    expect(await screen.findByRole("button", { name: "Войти через Google" })).toBeInTheDocument();
  });

  it("shows why the last login failed", async () => {
    setup(KEYS, { "POST /api/youtube/account/verify": { body: st({ error: "youtube_no_channel" }) } });
    expect(await screen.findByRole("alert")).toHaveTextContent(/нет канала YouTube/);
  });

  it("saves the client id and shows a field error", async () => {
    const { fetch } = setup(makeSettings(), {
      "POST /api/youtube/account/verify": { body: st() },
      "PATCH /api/settings": {
        status: 422,
        body: {
          code: "validation_error",
          params: { fields: { "body.youtube.client_id": "invalid_client_id" } },
        },
      },
    });
    await userEvent.type(screen.getByLabelText("Client ID"), "nope");
    await userEvent.click(screen.getAllByRole("button", { name: "Сохранить" })[0]);
    expect(await screen.findByText(/Client ID выглядит как/)).toBeInTheDocument();
    expect(calls(fetch).find((c) => c.method === "PATCH")?.body).toEqual({
      youtube: { client_id: "nope" },
    });
  });

  it("saves the upload defaults", async () => {
    const { fetch, onSaved } = setup(KEYS, {
      "POST /api/youtube/account/verify": { body: st() },
      "PATCH /api/settings": ({ body }) => ({
        body: { ...KEYS, youtube: { ...KEYS.youtube, ...(body as { youtube: object }).youtube } },
      }),
    });
    await userEvent.selectOptions(screen.getByLabelText("Доступ"), "unlisted");
    await userEvent.click(screen.getByLabelText("Да"));
    await userEvent.selectOptions(screen.getByLabelText("Категория"), "27");
    await userEvent.type(screen.getByLabelText("Теги"), "#горы, влог, горы");
    const buttons = screen.getAllByRole("button", { name: "Сохранить" });
    await userEvent.click(buttons[buttons.length - 1]);

    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(calls(fetch).find((c) => c.method === "PATCH")?.body).toEqual({
      youtube: {
        privacy: "unlisted",
        made_for_kids: true,
        category_id: "27",
        tags: ["горы", "влог"],
      },
    });
    expect(screen.getByLabelText("Теги")).toHaveValue("горы, влог");
  });
});

describe("youtube settings: dead login", () => {
  it("shows the login button when Google no longer accepts the stored login", async () => {
    const { onSaved } = setup(
      makeSettings({
        ...KEYS,
        youtube: { ...KEYS.youtube, channel_id: "UC1", channel_title: "My Channel" },
        secrets: { youtube_client_secret: true, youtube_refresh_token: true },
      }),
      { "POST /api/youtube/account/verify": { body: st({ error: "youtube_token_invalid" }) } },
    );
    expect(await screen.findByRole("button", { name: "Войти через Google" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent(/больше не действует/);
    expect(onSaved).toHaveBeenLastCalledWith(
      expect.objectContaining({
        secrets: expect.objectContaining({ youtube_refresh_token: false }),
      }),
    );
  });
});

describe("parseTags", () => {
  it("splits by commas, drops #, blanks and repeats", () => {
    expect(parseTags(" #a, b c ,, a,")).toEqual(["a", "b c"]);
    expect(parseTags("")).toEqual([]);
  });
});
