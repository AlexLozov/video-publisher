import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import App from "./App";
import { setLanguage, STORAGE_KEY } from "./i18n";
import { mockFetch } from "./test/fetchMock";

const PLATFORMS = [
  { id: "youtube", available: false },
  { id: "facebook", available: false },
  { id: "telegram", available: true },
];

const ok = () =>
  mockFetch({
    "/api/health": { body: { status: "ok", version: "1.2.3" } },
    "/api/platforms": { body: PLATFORMS },
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
      "/api/platforms": { body: PLATFORMS },
    });
    render(<App />);
    expect(await screen.findByText("Сервер недоступен")).toBeInTheDocument();
  });

  it("shows an error when the network fails", async () => {
    mockFetch({
      "/api/health": { error: new TypeError("Failed to fetch") },
      "/api/platforms": { error: new TypeError("Failed to fetch") },
    });
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
      "/api/health": { body: { status: "ok", version: "0.1.0" } },
      "/api/platforms": { body: [{ id: "mastodon", available: false }] },
    });
    render(<App />);
    expect(await screen.findByText("mastodon")).toBeInTheDocument();
  });

  it("shows a placeholder when the platforms list is empty", async () => {
    mockFetch({
      "/api/health": { body: { status: "ok", version: "0.1.0" } },
      "/api/platforms": { body: [] },
    });
    render(<App />);
    await screen.findByText(/Сервер работает/);
    expect(screen.getByText("Список платформ недоступен")).toBeInTheDocument();
  });
});

describe("language switcher", () => {
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

  it("switches the whole screen to English and saves the choice", async () => {
    ok();
    render(<App />);
    await screen.findByText("Сервер работает · v1.2.3");

    await userEvent.click(screen.getByRole("button", { name: "EN" }));

    expect(await screen.findByText("Server is running · v1.2.3")).toBeInTheDocument();
    expect(screen.getByText("Publish one video to several social networks at once")).toBeVisible();
    expect(screen.getByText("Facebook Page")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Interface language" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "EN" })).toHaveAttribute("aria-pressed", "true");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("en");
  });

  it("switches back to Russian", async () => {
    ok();
    await setLanguage("en");
    render(<App />);
    await screen.findByText("Server is running · v1.2.3");

    await userEvent.click(screen.getByRole("button", { name: "RU" }));

    expect(await screen.findByText("Сервер работает · v1.2.3")).toBeInTheDocument();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("ru");
  });

  it("leaves no Russian text on screen in English (no hardcoded strings)", async () => {
    ok();
    await setLanguage("en");
    const { container } = render(<App />);
    await screen.findByText("Server is running · v1.2.3");
    // Language names are shown in their own language on purpose, so skip the switcher.
    const group = screen.getByRole("group");
    group.remove();
    expect(container.textContent).not.toMatch(/[А-Яа-яЁё]/);
  });
});
