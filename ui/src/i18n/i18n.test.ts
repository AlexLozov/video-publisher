import { describe, expect, it, vi } from "vitest";

import i18n, {
  DEFAULT_LANGUAGE,
  getStoredLanguage,
  isLanguage,
  setLanguage,
  STORAGE_KEY,
} from ".";

describe("i18n", () => {
  it("defaults to Russian", () => {
    expect(DEFAULT_LANGUAGE).toBe("ru");
    expect(getStoredLanguage()).toBe("ru");
    expect(i18n.t("status.down")).toBe("Сервер недоступен");
  });

  it("switches language and persists the choice", async () => {
    await setLanguage("en");
    expect(i18n.language).toBe("en");
    expect(i18n.t("status.down")).toBe("Server is unavailable");
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("en");
    expect(getStoredLanguage()).toBe("en");
  });

  it("updates the <html lang> attribute", async () => {
    await setLanguage("en");
    expect(document.documentElement.lang).toBe("en");
    await setLanguage("ru");
    expect(document.documentElement.lang).toBe("ru");
  });

  it("ignores an unsupported stored value", () => {
    window.localStorage.setItem(STORAGE_KEY, "de");
    expect(getStoredLanguage()).toBe("ru");
  });

  it("falls back to default when storage is unavailable", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    expect(getStoredLanguage()).toBe("ru");
  });

  it("still switches language when storage cannot be written", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota");
    });
    await setLanguage("en");
    expect(i18n.language).toBe("en");
  });

  it("interpolates parameters", async () => {
    await setLanguage("en");
    expect(i18n.t("status.ok", { version: "1.0.0" })).toBe("Server is running · v1.0.0");
  });

  it("validates language codes", () => {
    expect(isLanguage("ru")).toBe(true);
    expect(isLanguage("en")).toBe(true);
    expect(isLanguage("de")).toBe(false);
    expect(isLanguage(null)).toBe(false);
  });
});
