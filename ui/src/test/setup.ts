import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import i18n, { DEFAULT_LANGUAGE } from "../i18n";

beforeEach(async () => {
  window.localStorage.clear();
  await i18n.changeLanguage(DEFAULT_LANGUAGE);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
