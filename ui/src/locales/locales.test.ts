import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { resources, SUPPORTED_LANGUAGES } from "../i18n";

const LOCALES_DIR = path.dirname(fileURLToPath(import.meta.url));

type Tree = { [key: string]: string | Tree };

function flatten(tree: Tree, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree)) {
    const full = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") {
      out[full] = value;
    } else {
      Object.assign(out, flatten(value, full));
    }
  }
  return out;
}

const placeholders = (s: string) => [...s.matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((m) => m[1]).sort();

const flat = Object.fromEntries(
  SUPPORTED_LANGUAGES.map((lng) => [lng, flatten(resources[lng].translation as Tree)]),
);
const reference = flat.ru;

describe("locale files", () => {
  it("supports Russian and English", () => {
    expect(SUPPORTED_LANGUAGES).toEqual(["ru", "en"]);
  });

  it("every locale file in src/locales is registered", () => {
    const files = readdirSync(LOCALES_DIR)
      .filter((f) => f.endsWith(".json"))
      .map((f) => f.replace(".json", ""))
      .sort();
    expect(files).toEqual([...SUPPORTED_LANGUAGES].sort());
  });

  it.each(SUPPORTED_LANGUAGES)("%s has exactly the same keys as ru", (lng) => {
    expect(Object.keys(flat[lng]).sort()).toEqual(Object.keys(reference).sort());
  });

  it.each(SUPPORTED_LANGUAGES)("%s has no empty strings", (lng) => {
    const empty = Object.entries(flat[lng]).filter(([, v]) => v.trim() === "");
    expect(empty).toEqual([]);
  });

  it.each(SUPPORTED_LANGUAGES)("%s uses the same {{placeholders}} as ru", (lng) => {
    for (const [key, value] of Object.entries(reference)) {
      expect(placeholders(flat[lng][key]), key).toEqual(placeholders(value));
    }
  });

  it("has a name for every platform the backend knows", () => {
    for (const id of ["youtube", "tiktok", "instagram", "facebook", "telegram"]) {
      expect(Object.keys(reference)).toContain(`platform.${id}`);
    }
  });

  it("has a message for every backend error code", () => {
    const backendCodes = [
      "not_found",
      "method_not_allowed",
      "http_error",
      "validation_error",
      "internal_error",
    ];
    for (const code of backendCodes) {
      expect(Object.keys(reference)).toContain(`errors.${code}`);
    }
  });

  it("files are valid JSON", () => {
    for (const lng of SUPPORTED_LANGUAGES) {
      expect(() => JSON.parse(readFileSync(path.join(LOCALES_DIR, `${lng}.json`), "utf-8"))).not.toThrow();
    }
  });
});
