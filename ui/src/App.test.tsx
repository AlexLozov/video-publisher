import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import App from "./App";
import i18n, { setLanguage, STORAGE_KEY } from "./i18n";
import { calls, mockFetch } from "./test/fetchMock";
import { HEALTH_OK, makeSettings, PLATFORMS } from "./test/fixtures";

const ok = (settings = makeSettings()) =>
  mockFetch({
    "/api/health": HEALTH_OK,
    "/api/platforms": PLATFORMS,
    "GET /api/settings": { body: settings },
    "PATCH /api/settings": ({ body }) => ({
      body: { ...settings, ...(body as object) },
    }),
  });

describe("start screen", () => {
  it("shows the title and a loading state first", () => {
    mockFetch({});
    render(<App />);
    expect(screen.getByRole("heading", { name: "Video Publisher" })).toBeInTheDocument();
    expect(screen.getByText("Проверяем сервер…")).toBeInTheDocument();
  });

  it("shows backend version when health is OK", async () => {
    ok();
    render(<App />);
    expect(await screen.findByText("Сервер работает · v1.2.3")).toBeInTheDocument();
  });

  it("shows an error when health returns HTTP error", async () => {
    mockFetch({
      "/api/health": { status: 500, body: { code: "internal_error", params: {} } },
      "/api/platforms": PLATFORMS,
    });
    render(<App />);
    expect(await screen.findByText("Сервер недоступен")).toBeInTheDocument();
  });

  it("shows an error when the network fails", async () => {
    const down = { error: new TypeError("Failed to fetch") };
    mockFetch({ "/api/health": down, "/api/platforms": down, "/api/settings": down });
    render(<App />);
    expect(await screen.findByText("Сервер недоступен")).toBeInTheDocument();
    expect(screen.getByText("Список платформ недоступен")).toBeInTheDocument();
  });

  it("lists translated platform names with their availability", async () => {
    ok();
    render(<App />);
    expect(await screen.findByText("YouTube / Shorts")).toBeInTheDocument();
    expect(screen.getByText("Страница Facebook")).toBeInTheDocument();
    expect(screen.getByText("Telegram")).toBeInTheDocument();
    expect(screen.getAllByText("в разработке")).toHaveLength(2);
    expect(screen.getByText("подключено")).toBeInTheDocument();
  });

  it("falls back to the raw id for an unknown platform", async () => {
    mockFetch({
      "/api/health": HEALTH_OK,
      "/api/platforms": { body: [{ id: "mastodon", available: false }] },
    });
    render(<App />);
    expect(await screen.findByText("mastodon")).toBeInTheDocument();
  });

  it("shows a placeholder when the platforms list is empty", async () => {
    mockFetch({ "/api/health": HEALTH_OK, "/api/platforms": { body: [] } });
    render(<App />);
    await screen.findByText(/Сервер работает/);
    expect(screen.getByText("Список платформ недоступен")).toBeInTheDocument();
  });
});

describe("navigation", () => {
  it("switches between home and settings", async () => {
    ok();
    render(<App />);
    const nav = screen.getByRole("navigation", { name: "Разделы" });
    expect(within(nav).getByRole("button", { name: "Главная" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await userEvent.click(within(nav).getByRole("button", { name: "Настройки" }));
    expect(await screen.findByRole("heading", { name: "Telegram" })).toBeInTheDocument();
    expect(within(nav).getByRole("button", { name: "Настройки" })).toHaveAttribute(
      "aria-current",
      "page",
    );

    await userEvent.click(within(nav).getByRole("button", { name: "Главная" }));
    expect(await screen.findByText("Сервер работает · v1.2.3")).toBeInTheDocument();
  });
});

describe("language", () => {
  it("marks Russian as active by default", () => {
    mockFetch({});
    render(<App />);
    const group = screen.getByRole("group", { name: "Язык интерфейса" });
    expect(within(group).getByRole("button", { name: "RU" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(group).getByRole("button", { name: "EN" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("applies the language saved on the backend", async () => {
    ok(makeSettings({ language: "en" }));
    render(<App />);
    expect(await screen.findByText("Server is running · v1.2.3")).toBeInTheDocument();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("en");
  });

  it("switches the whole screen to English and saves it on the backend", async () => {
    const fetch = ok();
    render(<App />);
    await screen.findByText("Сервер работает · v1.2.3");

    await userEvent.click(screen.getByRole("button", { name: "EN" }));

    expect(await screen.findByText("Server is running · v1.2.3")).toBeInTheDocument();
    expect(screen.getByText("Publish one video to several social networks at once")).toBeVisible();
    expect(screen.getByText("Facebook Page")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "EN" })).toHaveAttribute("aria-pressed", "true");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("en");
    await waitFor(() =>
      expect(calls(fetch)).toContainEqual({
        method: "PATCH",
        url: "/api/settings",
        body: { language: "en" },
      }),
    );
  });

  it("still switches locally when the backend cannot save", async () => {
    mockFetch({
      "/api/health": HEALTH_OK,
      "/api/platforms": PLATFORMS,
      "GET /api/settings": { body: makeSettings() },
      "PATCH /api/settings": { status: 503, body: { code: "internal_error", params: {} } },
    });
    render(<App />);
    await screen.findByText("Сервер работает · v1.2.3");
    await userEvent.click(screen.getByRole("button", { name: "EN" }));
    expect(await screen.findByText("Server is running · v1.2.3")).toBeInTheDocument();
    expect(i18n.language).toBe("en");
  });

  it("switches back to Russian", async () => {
    ok(makeSettings({ language: "en" }));
    await setLanguage("en");
    render(<App />);
    await screen.findByText("Server is running · v1.2.3");

    await userEvent.click(screen.getByRole("button", { name: "RU" }));

    expect(await screen.findByText("Сервер работает · v1.2.3")).toBeInTheDocument();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("ru");
  });

  it.each(["home", "settings"] as const)(
    "leaves no Russian text on the %s screen in English (no hardcoded strings)",
    async (page) => {
      ok(makeSettings({ language: "en" }));
      await setLanguage("en");
      const { container } = render(<App />);
      await screen.findByText("Server is running · v1.2.3");
      if (page === "settings") {
        await userEvent.click(screen.getByRole("button", { name: "Settings" }));
        await screen.findByRole("heading", { name: "Telegram" });
        // Account mode shows the remaining texts.
        await userEvent.click(screen.getByRole("radio", { name: /My account/ }));
      }
      // Language names are shown in their own language on purpose.
      container.querySelectorAll("[lang]").forEach((el) => el.remove());
      expect(container.textContent).not.toMatch(/[А-Яа-яЁё]/);
    },
  );
});
