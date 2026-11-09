import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Publication, Settings } from "../api";
import { FILE_DROPPED_EVENT } from "../desktop";
import { setLanguage } from "../i18n";
import { calls, mockFetch, type Route } from "../test/fetchMock";
import { makeSettings } from "../test/fixtures";
import { PublicationList } from "./PublicationList";
import { PublishPage } from "./PublishPage";

const CONFIGURED = makeSettings({
  telegram: { mode: "bot", channel: "@my_channel", api_id: null },
  secrets: { telegram_bot_token: true, telegram_api_hash: false, telegram_session: false },
});

function pub(overrides: Partial<Publication> = {}): Publication {
  return {
    id: "p1",
    batch_id: "b1",
    platform: "telegram",
    method: "bot",
    target: "@my_channel",
    video_path: "C:\\Videos\\clip.mp4",
    title: "",
    status: "published",
    scheduled_at: null,
    url: "https://t.me/my_channel/42",
    error_code: null,
    error_params: {},
    created_at: "2026-11-07T10:00:00Z",
    updated_at: "2026-11-07T10:00:00Z",
    ...overrides,
  };
}

function setup(
  routes: Record<string, Route> = {},
  settings: Settings | null = CONFIGURED,
  onOpenSettings = vi.fn(),
) {
  const fetch = mockFetch({ "GET /api/publications?limit=20": { body: [] }, ...routes });
  render(<PublishPage settings={settings} onOpenSettings={onOpenSettings} pollMs={10} />);
  return { fetch, onOpenSettings };
}

afterEach(() => {
  delete window.pywebview;
});

describe("publish page: choosing a video", () => {
  it("in browser mode asks for a path", async () => {
    setup();
    const input = screen.getByLabelText("Путь к видеофайлу");
    await userEvent.type(input, "C:\\Videos\\clip.mp4");
    expect(input).toHaveValue("C:\\Videos\\clip.mp4");
    expect(screen.queryByTestId("dropzone")).not.toBeInTheDocument();
  });

  it("in the desktop app opens the native dialog", async () => {
    const pick = vi.fn().mockResolvedValue("C:\\Videos\\clip.mp4");
    window.pywebview = { api: { pick_video: pick } };
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Выбрать видео…" }));
    expect(pick).toHaveBeenCalledOnce();
    expect(await screen.findByText("clip.mp4")).toBeInTheDocument();
    expect(screen.getByTestId("video-path")).toHaveTextContent("C:\\Videos\\clip.mp4");
    expect(screen.getByRole("button", { name: "Выбрать другое…" })).toBeInTheDocument();
  });

  it("keeps the previous choice when the dialog is cancelled", async () => {
    const pick = vi
      .fn()
      .mockResolvedValueOnce("C:\\Videos\\first.mp4")
      .mockResolvedValueOnce(null);
    window.pywebview = { api: { pick_video: pick } };
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Выбрать видео…" }));
    await screen.findByText("first.mp4");
    await userEvent.click(screen.getByRole("button", { name: "Выбрать другое…" }));
    expect(screen.getByText("first.mp4")).toBeInTheDocument();
  });

  it("shows an error when the native dialog fails", async () => {
    window.pywebview = { api: { pick_video: vi.fn().mockRejectedValue(new Error("bad filter")) } };
    setup();
    await userEvent.click(screen.getByRole("button", { name: "Выбрать видео…" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось открыть окно выбора файла",
    );
  });

  it("accepts a file dropped onto the window", async () => {
    window.pywebview = { api: { pick_video: vi.fn() } };
    setup();
    act(() => {
      window.dispatchEvent(
        new CustomEvent(FILE_DROPPED_EVENT, { detail: "D:\\Clips\\dropped.mp4" }),
      );
    });
    expect(await screen.findByText("dropped.mp4")).toBeInTheDocument();
  });

  it("disables publishing until a video is chosen", () => {
    setup();
    expect(screen.getByRole("button", { name: "Опубликовать сейчас" })).toBeDisabled();
  });
});

describe("publish page: destination", () => {
  it("shows the configured channel", () => {
    setup();
    expect(screen.getByTestId("destination")).toHaveTextContent("Telegram · Бот · @my_channel");
  });

  it.each([
    ["no settings yet", null],
    ["no channel", makeSettings({ secrets: CONFIGURED.secrets })],
    ["no token", makeSettings({ telegram: CONFIGURED.telegram })],
  ])("offers settings when not configured (%s)", async (_name, settings) => {
    const { onOpenSettings } = setup({}, settings);
    expect(screen.getByText("Telegram-бот не настроен.")).toBeInTheDocument();
    // Telegram's link comes first (Facebook, not set up either, has its own).
    await userEvent.click(screen.getAllByRole("button", { name: "Открыть настройки" })[0]);
    expect(onOpenSettings).toHaveBeenCalledOnce();
  });
});

describe("publish page: choosing networks", () => {
  it("ticks every set-up network and greys out the rest", () => {
    setup();
    expect(screen.getByTestId("network-telegram")).toBeChecked();
    expect(screen.getByTestId("network-telegram")).toBeEnabled();
    for (const p of ["youtube", "tiktok", "instagram", "facebook"]) {
      expect(screen.getByTestId(`network-${p}`)).toBeDisabled();
      expect(screen.getByTestId(`network-${p}`)).not.toBeChecked();
    }
    expect(screen.getAllByText("Скоро")).toHaveLength(2);
    expect(screen.getByText(/Facebook не настроен/)).toBeInTheDocument();
    expect(screen.getByText(/YouTube не настроен/)).toBeInTheDocument();
  });

  it("leaves a network that is not set up unticked", () => {
    setup({}, makeSettings());
    expect(screen.getByTestId("network-telegram")).not.toBeChecked();
    expect(screen.getByTestId("network-telegram")).toBeDisabled();
  });

  it("cannot publish with no network ticked", async () => {
    setup();
    await userEvent.type(screen.getByLabelText("Путь к видеофайлу"), "C:\\a.mp4");
    const button = screen.getByRole("button", { name: "Опубликовать сейчас" });
    expect(button).toBeEnabled();
    await userEvent.click(screen.getByTestId("network-telegram"));
    expect(button).toBeDisabled();
    await userEvent.click(screen.getByTestId("network-telegram"));
    expect(button).toBeEnabled();
  });

  it("names the network in an error", async () => {
    setup({
      "POST /api/publications/batch": {
        status: 422,
        body: { code: "video_not_found", params: { platform: "telegram" } },
      },
    });
    await userEvent.type(screen.getByLabelText("Путь к видеофайлу"), "C:\\a.mp4");
    await userEvent.click(screen.getByRole("button", { name: "Опубликовать сейчас" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/^Telegram: Файл не найден/);
  });

  it("hides scheduling when Telegram is unticked", async () => {
    setup({}, ACCOUNT);
    expect(screen.getByText("По расписанию")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("network-telegram"));
    expect(screen.queryByText("По расписанию")).not.toBeInTheDocument();
  });
});

describe("publish page: caption", () => {
  it("counts the caption like the backend and blocks when too long", async () => {
    setup();
    await userEvent.type(screen.getByLabelText("Путь к видеофайлу"), "C:\\a.mp4");
    await userEvent.type(screen.getByLabelText("Заголовок"), "Hi");
    await userEvent.type(screen.getByLabelText("Хэштеги"), "#a b");
    // "Hi\n\n#a #b" = 9
    expect(screen.getByTestId("caption-counter")).toHaveTextContent("Подпись: 9 / 1024 символов");

    await userEvent.click(screen.getByLabelText("Текст"));
    await userEvent.paste("x".repeat(1020));
    expect(screen.getByTestId("caption-counter")).toHaveClass("text-rose-600");
    expect(screen.getByRole("button", { name: "Опубликовать сейчас" })).toBeDisabled();
  });
});

describe("publish page: publishing", () => {
  it("sends the request, clears the form and shows progress until published", async () => {
    let listCalls = 0;
    const { fetch } = setup({
      "POST /api/publications/batch": { status: 202, body: [pub({ status: "publishing", url: null })] },
      "GET /api/publications?limit=20": () => {
        listCalls += 1;
        if (listCalls === 1) return { body: [] };
        if (listCalls === 2) return { body: [pub({ status: "publishing", url: null })] };
        return { body: [pub({ title: "Hello" })] };
      },
    });
    await screen.findByText("Пока ничего не опубликовано");

    await userEvent.type(screen.getByLabelText("Путь к видеофайлу"), "  C:\\Videos\\clip.mp4 ");
    await userEvent.type(screen.getByLabelText("Заголовок"), "Hello");
    await userEvent.type(screen.getByLabelText("Текст"), "World");
    await userEvent.type(screen.getByLabelText("Хэштеги"), "#cats, dogs");
    await userEvent.click(screen.getByRole("button", { name: "Опубликовать сейчас" }));

    expect(calls(fetch)).toContainEqual({
      method: "POST",
      url: "/api/publications/batch",
      body: {
        video_path: "C:\\Videos\\clip.mp4",
        platforms: ["telegram"],
        title: "Hello",
        description: "World",
        hashtags: ["cats", "dogs"],
      },
    });
    expect(await screen.findByText("публикуется…")).toBeInTheDocument();
    expect(await screen.findByText("опубликовано")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Открыть пост" })).toHaveAttribute(
      "href",
      "https://t.me/my_channel/42",
    );
    expect(screen.getByLabelText("Путь к видеофайлу")).toHaveValue("");
    expect(screen.getByLabelText("Заголовок")).toHaveValue("");
  });

  it.each([
    ["telegram_unsupported_format", {}, "Нужен файл MP4"],
    ["telegram_bot_file_too_large", {}, "Файл больше 50 МБ"],
    ["video_not_found", {}, "Файл не найден"],
    ["telegram_not_configured", { missing: ["bot_token"] }, "Telegram не настроен"],
    ["caption_too_long", { max: 1024, length: 2000 }, "длиннее 1024 символов (2000)"],
  ])("shows the translated error %s and keeps the form", async (code, params, text) => {
    setup({ "POST /api/publications/batch": { status: 422, body: { code, params } } });
    await userEvent.type(screen.getByLabelText("Путь к видеофайлу"), "C:\\a.mov");
    await userEvent.click(screen.getByRole("button", { name: "Опубликовать сейчас" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
    expect(screen.getByLabelText("Путь к видеофайлу")).toHaveValue("C:\\a.mov");
  });

  it("shows a network error when the backend is unreachable", async () => {
    setup({ "POST /api/publications/batch": { error: new TypeError("offline") } });
    await userEvent.type(screen.getByLabelText("Путь к видеофайлу"), "C:\\a.mp4");
    await userEvent.click(screen.getByRole("button", { name: "Опубликовать сейчас" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с сервером");
  });

  it("falls back to a generic message for an unknown error code", async () => {
    setup({ "POST /api/publications/batch": { status: 422, body: { code: "brand_new", params: {} } } });
    await userEvent.type(screen.getByLabelText("Путь к видеофайлу"), "C:\\a.mp4");
    await userEvent.click(screen.getByRole("button", { name: "Опубликовать сейчас" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Неизвестная ошибка");
  });

  it("is fully translated to English", async () => {
    await setLanguage("en");
    mockFetch({ "GET /api/publications?limit=20": { body: [] } });
    const { container } = render(
      <PublishPage settings={null} onOpenSettings={vi.fn()} pollMs={10} />,
    );
    expect(screen.getByRole("button", { name: "Publish now" })).toBeInTheDocument();
    await screen.findByText("Nothing published yet");
    expect(container.textContent).not.toMatch(/[А-Яа-яЁё]/);
  });
});

describe("publication list", () => {
  it("shows failures with translated, parameterised messages", async () => {
    mockFetch({
      "GET /api/publications?limit=20": {
        body: [
          pub({
            id: "a",
            status: "failed",
            url: null,
            error_code: "telegram_rate_limited",
            error_params: { retry_after: 30 },
          }),
          pub({ id: "b", status: "failed", url: null, error_code: "interrupted" }),
          pub({ id: "c", status: "failed", url: null, error_code: "something_new" }),
        ],
      },
    });
    render(<PublicationList refreshKey={0} pollMs={10} />);
    expect(await screen.findByText("Telegram просит подождать 30 с. Попробуйте позже.")).toBeVisible();
    expect(screen.getByText(/Приложение закрылось во время загрузки/)).toBeVisible();
    expect(screen.getByText("Неизвестная ошибка")).toBeVisible();
  });

  it("uses the file name when there is no title and shows target", async () => {
    mockFetch({ "GET /api/publications?limit=20": { body: [pub()] } });
    render(<PublicationList refreshKey={0} pollMs={10} />);
    const card = await screen.findByTestId("history-card");
    expect(within(card).getByText("clip.mp4")).toBeInTheDocument();
    const item = within(card).getByTestId("publication");
    expect(within(item).getByText("Telegram")).toBeInTheDocument();
    expect(within(item).getByText("@my_channel")).toBeInTheDocument();
  });

  it("does not show a link for a published item without url", async () => {
    mockFetch({ "GET /api/publications?limit=20": { body: [pub({ url: null })] } });
    render(<PublicationList refreshKey={0} pollMs={10} />);
    await screen.findByText("опубликовано");
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("stops polling once nothing is publishing", async () => {
    let n = 0;
    const fetch = mockFetch({
      "GET /api/publications?limit=20": () => {
        n += 1;
        return { body: [pub({ status: n < 3 ? "publishing" : "published" })] };
      },
    });
    render(<PublicationList refreshKey={0} pollMs={5} />);
    await screen.findByText("опубликовано");
    const count = fetch.mock.calls.length;
    await new Promise((r) => setTimeout(r, 50));
    expect(fetch.mock.calls.length).toBe(count);
    expect(count).toBe(3);
  });

  it("shows an error and retries when the list cannot be loaded", async () => {
    let n = 0;
    mockFetch({
      "GET /api/publications?limit=20": () => {
        n += 1;
        return n === 1 ? { status: 500, body: { code: "internal_error" } } : { body: [] };
      },
    });
    render(<PublicationList refreshKey={0} pollMs={5} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Не удалось загрузить список публикаций",
    );
    await waitFor(() => expect(screen.getByText("Пока ничего не опубликовано")).toBeVisible());
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("stops polling after unmount", async () => {
    const fetch = mockFetch({
      "GET /api/publications?limit=20": { body: [pub({ status: "publishing" })] },
    });
    const { unmount } = render(<PublicationList refreshKey={0} pollMs={5} />);
    await screen.findByText("публикуется…");
    unmount();
    const count = fetch.mock.calls.length;
    await new Promise((r) => setTimeout(r, 40));
    expect(fetch.mock.calls.length).toBe(count);
  });
});

const ACCOUNT = makeSettings({
  telegram: { mode: "account", channel: "@my_channel", api_id: 4242 },
  secrets: { telegram_bot_token: false, telegram_api_hash: true, telegram_session: true },
});

describe("publish page: my account mode", () => {
  it("shows the account destination and the 2 GB limit", () => {
    setup({}, ACCOUNT);
    expect(screen.getByTestId("destination")).toHaveTextContent(
      "Telegram · Мой аккаунт · @my_channel",
    );
    expect(screen.getByText("Для Telegram через аккаунт: MP4 до 2 ГБ.")).toBeInTheDocument();
  });

  it("asks to finish account setup when not signed in", () => {
    setup(
      {},
      makeSettings({ ...ACCOUNT, secrets: { ...ACCOUNT.secrets, telegram_session: false } }),
    );
    expect(screen.getByText(/Telegram-аккаунт не настроен/)).toBeInTheDocument();
  });

  it("offers scheduling only in account mode", () => {
    setup();
    expect(screen.queryByText("По расписанию")).not.toBeInTheDocument();
  });

  it("publishes now without a time", async () => {
    const { fetch } = setup(
      { "POST /api/publications/batch": { status: 202, body: [pub({ status: "publishing" })] } },
      ACCOUNT,
    );
    await userEvent.type(screen.getByLabelText("Путь к видеофайлу"), "C:\a.mp4");
    await userEvent.click(screen.getByRole("button", { name: "Опубликовать сейчас" }));
    await waitFor(() =>
      expect(calls(fetch).some((c) => c.method === "POST")).toBe(true),
    );
    const post = calls(fetch).find((c) => c.method === "POST");
    expect(post?.body).not.toHaveProperty("scheduled_at");
  });

  it("schedules at the chosen local time, sent as UTC", async () => {
    const { fetch } = setup(
      { "POST /api/publications/batch": { status: 202, body: [pub({ status: "publishing" })] } },
      ACCOUNT,
    );
    await userEvent.type(screen.getByLabelText("Путь к видеофайлу"), "C:\a.mp4");
    await userEvent.click(screen.getByLabelText("По расписанию"));
    const button = screen.getByRole("button", { name: "Запланировать" });
    expect(button).toBeDisabled(); // no time yet

    const when = screen.getByLabelText("Дата и время публикации");
    await userEvent.type(when, "2026-12-01T18:30");
    expect(button).toBeEnabled();
    await userEvent.click(button);

    await waitFor(() =>
      expect(calls(fetch).some((c) => c.method === "POST")).toBe(true),
    );
    const post = calls(fetch).find((c) => c.method === "POST");
    expect((post?.body as { scheduled_at: string }).scheduled_at).toBe(
      new Date("2026-12-01T18:30").toISOString(),
    );
    // Form is reset after success.
    await waitFor(() => expect(screen.getByLabelText("Сейчас")).toBeChecked());
  });

  it("shows a schedule error from the backend", async () => {
    setup(
      {
        "POST /api/publications/batch": {
          status: 422,
          body: { code: "schedule_in_past", params: { min_minutes: 1 } },
        },
      },
      ACCOUNT,
    );
    await userEvent.type(screen.getByLabelText("Путь к видеофайлу"), "C:\a.mp4");
    await userEvent.click(screen.getByLabelText("По расписанию"));
    await userEvent.type(screen.getByLabelText("Дата и время публикации"), "2020-01-01T10:00");
    await userEvent.click(screen.getByRole("button", { name: "Запланировать" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "хотя бы на 1 мин позже текущего",
    );
  });
});

describe("publication list: scheduled", () => {
  it("shows when a scheduled post goes out", async () => {
    const at = "2026-12-01T15:30:00Z";
    mockFetch({
      "GET /api/publications?limit=20": {
        body: [pub({ status: "scheduled", url: null, scheduled_at: at })],
      },
    });
    render(<PublicationList refreshKey={0} pollMs={10} />);
    expect(await screen.findByTestId("publication-status")).toHaveTextContent("запланировано");
    const expected = new Date(at).toLocaleString("ru", { dateStyle: "medium", timeStyle: "short" });
    expect(screen.getByTestId("publication-scheduled")).toHaveTextContent(
      `запланировано на ${expected}`,
    );
    expect(screen.queryByText("Открыть пост")).not.toBeInTheDocument();
  });
});

const FACEBOOK = makeSettings({
  ...CONFIGURED,
  facebook: { app_id: "123456789012", page_id: "55", page_name: "My Page" },
  secrets: { ...CONFIGURED.secrets, facebook_app_secret: true, facebook_page_token: true },
});

describe("publish page: facebook", () => {
  it("ticks a logged-in Facebook page and shows its name and limit", () => {
    setup({}, FACEBOOK);
    expect(screen.getByTestId("network-facebook")).toBeChecked();
    expect(screen.getByTestId("destination-facebook")).toHaveTextContent(
      "Страница Facebook · My Page",
    );
    expect(screen.getByText("Для Facebook: MP4 до 1 ГБ.")).toBeInTheDocument();
  });

  it("offers scheduling only when every ticked network can keep the post", async () => {
    setup({}, FACEBOOK);
    // Telegram bot is ticked too: it cannot hold a scheduled post.
    expect(screen.queryByText("По расписанию")).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId("network-telegram"));
    expect(screen.getByText("По расписанию")).toBeInTheDocument();
  });

  it("ignores the Telegram caption limit when only Facebook is ticked", async () => {
    setup({}, FACEBOOK);
    await userEvent.click(screen.getByTestId("network-telegram"));
    await userEvent.type(screen.getByLabelText("Путь к видеофайлу"), "C:\a.mp4");
    const long = "x".repeat(1100);
    await userEvent.click(screen.getByLabelText("Текст"));
    await userEvent.paste(long);
    expect(screen.getByRole("button", { name: "Опубликовать сейчас" })).toBeEnabled();
  });
});

const YOUTUBE = makeSettings({
  ...CONFIGURED,
  youtube: {
    ...makeSettings().youtube,
    client_id: "1-a.apps.googleusercontent.com",
    channel_id: "UC1",
    channel_title: "My Channel",
  },
  secrets: { ...CONFIGURED.secrets, youtube_client_secret: true, youtube_refresh_token: true },
});

const infoUrl = (path: string) =>
  `GET /api/publications/video-info?path=${encodeURIComponent(path)}`;

describe("publish page: youtube", () => {
  it("ticks a logged-in channel and tells whether the video goes out as a Short", async () => {
    setup(
      {
        [infoUrl("C:\v.mp4")]: {
          body: { duration_seconds: 42.4, width: 1080, height: 1920, youtube_short: true },
        },
      },
      YOUTUBE,
    );
    expect(screen.getByTestId("network-youtube")).toBeChecked();
    expect(screen.getByTestId("destination-youtube")).toHaveTextContent("YouTube / Shorts · My Channel");
    await userEvent.type(screen.getByLabelText("Путь к видеофайлу"), "C:\v.mp4");
    expect(await screen.findByTestId("youtube-kind")).toHaveTextContent(
      "YouTube: уйдёт как Shorts (вертикальное, 42 с).",
    );
  });

  it("says regular video for a landscape file and nothing when unknown", async () => {
    setup(
      {
        [infoUrl("C:\wide.mp4")]: {
          body: { duration_seconds: 600, width: 1920, height: 1080, youtube_short: false },
        },
      },
      YOUTUBE,
    );
    const input = screen.getByLabelText("Путь к видеофайлу");
    await userEvent.type(input, "C:\wide.mp4");
    expect(await screen.findByTestId("youtube-kind")).toHaveTextContent(
      "YouTube: уйдёт как обычное видео.",
    );
    await userEvent.type(input, "x"); // no such file: the hint disappears
    await waitFor(() => expect(screen.queryByTestId("youtube-kind")).not.toBeInTheDocument());
  });

  it("can schedule when only YouTube is ticked", async () => {
    setup({}, YOUTUBE);
    expect(screen.queryByText("По расписанию")).not.toBeInTheDocument();
    await userEvent.click(screen.getByTestId("network-telegram"));
    expect(screen.getByText("По расписанию")).toBeInTheDocument();
  });
});
