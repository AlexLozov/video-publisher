import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "../locales/en.json";
import ru from "../locales/ru.json";

/** To add a language: create src/locales/<code>.json and add it here. */
export const resources = {
  ru: { translation: ru },
  en: { translation: en },
} as const;

export type Language = keyof typeof resources;

export const SUPPORTED_LANGUAGES = Object.keys(resources) as Language[];
export const DEFAULT_LANGUAGE: Language = "ru";

// The choice is kept in localStorage for now.
// TODO: move it to backend settings once the storage layer exists.
export const STORAGE_KEY = "vp.language";

export function isLanguage(value: unknown): value is Language {
  return typeof value === "string" && (SUPPORTED_LANGUAGES as string[]).includes(value);
}

export function getStoredLanguage(): Language {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return isLanguage(stored) ? stored : DEFAULT_LANGUAGE;
  } catch {
    return DEFAULT_LANGUAGE;
  }
}

export async function setLanguage(language: Language): Promise<void> {
  try {
    window.localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // Storage may be unavailable (private mode); the language still switches for this session.
  }
  await i18n.changeLanguage(language);
}

i18n.on("languageChanged", (lng) => {
  document.documentElement.lang = lng;
});

void i18n.use(initReactI18next).init({
  resources,
  lng: getStoredLanguage(),
  fallbackLng: DEFAULT_LANGUAGE,
  supportedLngs: SUPPORTED_LANGUAGES,
  interpolation: { escapeValue: false },
  returnNull: false,
});

export default i18n;
