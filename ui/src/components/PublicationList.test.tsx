import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Publication } from "../api";
import { BOM, csvFileName, historyCsv } from "../csv";
import i18n from "../i18n";
import { calls, mockFetch, type Route } from "../test/fetchMock";
import { PublicationList } from "./PublicationList";

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

const LIST = "GET /api/publications?limit=20";

function setup(list: Publication[] | (() => Publication[]), routes: Record<string, Route> = {}) {
  const fetch = mockFetch({
    [LIST]: () => ({ body: typeof list === "function" ? list() : list }),
    ...routes,
  });
  render(<PublicationList refreshKey={0} pollMs={10} />);
  return fetch;
}

afterEach(() => {
  delete window.pywebview;
});

describe("history: cards", () => {
  it("shows one card per upload with every network inside", async () => {
    setup([
      pub({ id: "a", batch_id: "b1", title: "Cats" }),
      pub({ id: "b", batch_id: "b1", platform: "youtube", target: null, url: null, status: "failed", error_code: "network_error" }),
      pub({ id: "c", batch_id: "b2", video_path: "D:\\dogs.mp4" }),
    ]);
    const cards = await screen.findAllByTestId("history-card");
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByText("Cats")).toBeInTheDocument();
    expect(within(cards[0]).getAllByTestId("publication")).toHaveLength(2);
    expect(within(cards[0]).getByText("YouTube / Shorts")).toBeInTheDocument();
    expect(within(cards[1]).getByText("dogs.mp4")).toBeInTheDocument();
  });

  it("hides the toolbar when the history is empty", async () => {
    setup([]);
    await screen.findByText("Пока ничего не опубликовано");
    expect(screen.queryByRole("button", { name: "Очистить историю" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Сохранить в CSV" })).not.toBeInTheDocument();
  });
});

describe("history: deleting", () => {
  it("asks first, then deletes every record of the card", async () => {
    let list = [pub({ id: "a" }), pub({ id: "b", platform: "youtube" })];
    const fetch = setup(() => list, {
      "DELETE /api/publications/a": () => {
        list = list.filter((p) => p.id !== "a");
        return { status: 204 };
      },
      "DELETE /api/publications/b": () => {
        list = list.filter((p) => p.id !== "b");
        return { status: 204 };
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Удалить" }));
    expect(screen.getByText("Удалить запись?")).toBeInTheDocument();
    expect(calls(fetch).some((c) => c.method === "DELETE")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Да" }));
    await screen.findByText("Пока ничего не опубликовано");
    expect(calls(fetch).filter((c) => c.method === "DELETE").map((c) => c.url)).toEqual([
      "/api/publications/a",
      "/api/publications/b",
    ]);
  });

  it("can be cancelled", async () => {
    const fetch = setup([pub()]);
    await userEvent.click(await screen.findByRole("button", { name: "Удалить" }));
    await userEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.queryByText("Удалить запись?")).not.toBeInTheDocument();
    expect(calls(fetch).some((c) => c.method === "DELETE")).toBe(false);
  });

  it("warns that a scheduled post will be cancelled", async () => {
    setup([pub({ status: "scheduled", url: null, scheduled_at: "2026-12-01T10:00:00Z" })]);
    await userEvent.click(await screen.findByRole("button", { name: "Удалить" }));
    expect(screen.getByText("Запланированный пост будет отменён в соцсети.")).toBeInTheDocument();
  });

  it("shows why a record could not be deleted", async () => {
    setup([pub({ status: "scheduled", method: "account", url: null })], {
      "DELETE /api/publications/p1": {
        status: 422,
        body: { code: "cancel_needs_method", params: { method: "account" } },
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Удалить" }));
    await userEvent.click(screen.getByRole("button", { name: "Да" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Пост запланирован через способ");
    expect(screen.getByTestId("history-card")).toBeInTheDocument();
  });

  it("cannot delete while the upload is running", async () => {
    setup([pub({ status: "publishing", url: null })]);
    expect(await screen.findByRole("button", { name: "Удалить" })).toBeDisabled();
  });
});

describe("history: clearing", () => {
  it("asks first and reports how many records were removed", async () => {
    let list = [pub({ id: "a" }), pub({ id: "b", batch_id: "b2", status: "scheduled" })];
    const fetch = setup(() => list, {
      "DELETE /api/publications": () => {
        list = list.filter((p) => p.status === "scheduled");
        return { body: { deleted: 1 } };
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Очистить историю" }));
    expect(screen.getByText(/Запланированные останутся/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Да" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Удалено записей: 1");
    await waitFor(() => expect(screen.getAllByTestId("history-card")).toHaveLength(1));
    expect(calls(fetch)).toContainEqual({
      method: "DELETE",
      url: "/api/publications",
      body: undefined,
    });
  });

  it("shows an error when clearing fails", async () => {
    setup([pub()], { "DELETE /api/publications": { error: new TypeError("offline") } });
    await userEvent.click(await screen.findByRole("button", { name: "Очистить историю" }));
    await userEvent.click(screen.getByRole("button", { name: "Да" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Нет связи с сервером");
  });
});

describe("history: CSV", () => {
  it("saves every record through the Windows save dialog", async () => {
    const save = vi.fn().mockResolvedValue("C:\\Users\\me\\history.csv");
    window.pywebview = { api: { pick_video: vi.fn(), save_csv: save } };
    const all = [pub({ id: "a", title: "Hi; \"there\"" }), pub({ id: "b" })];
    setup([pub()], { "GET /api/publications?limit=1000": { body: all } });

    await userEvent.click(await screen.findByRole("button", { name: "Сохранить в CSV" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Файл сохранён: C:\\Users\\me\\history.csv",
    );
    const [name, content] = save.mock.calls[0] as [string, string];
    expect(name).toMatch(/^history-\d{4}-\d{2}-\d{2}\.csv$/);
    expect(content.startsWith(BOM)).toBe(true);
    const lines = content.slice(1).split("\r\n");
    expect(lines[0]).toBe(
      "Загружено;Файл;Заголовок;Соцсеть;Способ;Канал / аккаунт;Статус;Запланировано на;Ссылка;Ошибка",
    );
    expect(lines[1]).toContain(';"Hi; ""there""";Telegram;Бот;@my_channel;опубликовано;;');
    expect(lines).toHaveLength(4); // header, 2 rows, trailing empty line
  });

  it("says nothing when the dialog is cancelled", async () => {
    window.pywebview = { api: { pick_video: vi.fn(), save_csv: vi.fn().mockResolvedValue(null) } };
    setup([pub()], { "GET /api/publications?limit=1000": { body: [pub()] } });
    await userEvent.click(await screen.findByRole("button", { name: "Сохранить в CSV" }));
    await waitFor(() => expect(window.pywebview?.api?.save_csv).toHaveBeenCalled());
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("reports a failed export", async () => {
    window.pywebview = {
      api: { pick_video: vi.fn(), save_csv: vi.fn().mockRejectedValue(new Error("disk")) },
    };
    setup([pub()], { "GET /api/publications?limit=1000": { body: [pub()] } });
    await userEvent.click(await screen.findByRole("button", { name: "Сохранить в CSV" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось сохранить файл");
  });

  it("downloads the file in a browser", async () => {
    const create = vi.fn(() => "blob:x");
    const revoke = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    setup([pub()], { "GET /api/publications?limit=1000": { body: [pub()] } });
    await userEvent.click(await screen.findByRole("button", { name: "Сохранить в CSV" }));
    await waitFor(() => expect(click).toHaveBeenCalledOnce());
    expect(revoke).toHaveBeenCalledWith("blob:x");
    click.mockRestore();
  });

  it("formats errors, methods and scheduled times", () => {
    const t = i18n.t.bind(i18n);
    const csv = historyCsv(
      [
        pub({
          method: "account",
          status: "failed",
          url: null,
          error_code: "network_error",
          scheduled_at: "2026-12-01T10:00:00Z",
        }),
      ],
      t,
    );
    const row = csv.split("\r\n")[1];
    expect(row).toContain(";Мой аккаунт;");
    expect(row).toContain(";ошибка;");
    expect(row).toMatch(/;2026-12-01 \d\d:00;;Нет связи/);
    expect(csvFileName(new Date(2026, 10, 7, 12))).toBe("history-2026-11-07.csv");
  });
});
