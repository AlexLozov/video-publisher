import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import App from "./App";
import { mockFetch } from "./test/fetchMock";

const PLATFORMS = [
  { id: "youtube", name: "YouTube / Shorts", available: false },
  { id: "telegram", name: "Telegram", available: true },
];

describe("start screen", () => {
  it("shows the title and a loading state first", () => {
    mockFetch({});
    render(<App />);
    expect(screen.getByRole("heading", { name: "Video Publisher" })).toBeInTheDocument();
    expect(screen.getByText("Проверяем сервер…")).toBeInTheDocument();
  });

  it("shows backend version when health is OK", async () => {
    mockFetch({
      "/api/health": { body: { status: "ok", version: "1.2.3" } },
      "/api/platforms": { body: PLATFORMS },
    });
    render(<App />);
    expect(await screen.findByText(/Сервер работает · v1\.2\.3/)).toBeInTheDocument();
  });

  it("shows an error when health returns HTTP error", async () => {
    mockFetch({
      "/api/health": { status: 500, body: {} },
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

  it("lists platforms with their availability", async () => {
    mockFetch({
      "/api/health": { body: { status: "ok", version: "0.1.0" } },
      "/api/platforms": { body: PLATFORMS },
    });
    render(<App />);
    expect(await screen.findByText("YouTube / Shorts")).toBeInTheDocument();
    expect(screen.getByText("Telegram")).toBeInTheDocument();
    expect(screen.getByText("в разработке")).toBeInTheDocument();
    expect(screen.getByText("подключено")).toBeInTheDocument();
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
