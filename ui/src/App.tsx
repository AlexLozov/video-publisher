import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchSettings, patchSettings, verifyYouTubeLogin, type Settings } from "./api";
import { HomePage } from "./components/HomePage";
import { LanguageSwitcher } from "./components/LanguageSwitcher";
import { PublishPage } from "./components/PublishPage";
import { SettingsPage } from "./components/SettingsPage";
import { errorKey } from "./errors";
import i18n, { setLanguage, type Language } from "./i18n";
import { isConfigured } from "./platforms";

type Page = "home" | "publish" | "settings";

export default function App() {
  const { t } = useTranslation();
  const [page, setPage] = useState<Page>("home");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [settingsError, setSettingsError] = useState<string | null>(null);

  useEffect(() => {
    fetchSettings()
      .then((s) => {
        setSettings(s);
        if (s.language !== i18n.resolvedLanguage) {
          void setLanguage(s.language);
        }
      })
      .catch((err: unknown) => setSettingsError(errorKey(err)));
  }, []);

  // A YouTube login can die on Google's side (7 days in "Testing", revoked access). Check it
  // when the app starts and when the upload or settings screen opens, so YouTube shows as
  // not set up right away instead of failing in the middle of an upload.
  const youtubeReady = isConfigured(settings, "youtube");
  useEffect(() => {
    if (!youtubeReady || page === "home") return;
    let cancelled = false;
    verifyYouTubeLogin()
      .then((status) => {
        if (cancelled || status.state === "logged_in") return;
        setSettings((s) =>
          s
            ? {
                ...s,
                youtube: { ...s.youtube, channel_id: null, channel_title: null },
                secrets: { ...s.secrets, youtube_refresh_token: false },
              }
            : s,
        );
      })
      .catch(() => {
        // offline: keep things as they are, the upload itself reports problems
      });
    return () => {
      cancelled = true;
    };
  }, [youtubeReady, page]);

  const changeLanguage = useCallback(async (lng: Language) => {
    await setLanguage(lng);
    try {
      setSettings(await patchSettings({ language: lng }));
    } catch {
      // Backend unavailable: the language still switches locally (cached in localStorage).
    }
  }, []);

  return (
    <div className="min-h-screen bg-slate-50 text-slate-800">
      <main className="mx-auto max-w-2xl px-6 py-12">
        <header className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-semibold tracking-tight">{t("app.title")}</h1>
            <p className="mt-1 text-slate-500">{t("app.subtitle")}</p>
          </div>
          <LanguageSwitcher onChange={(lng) => void changeLanguage(lng)} />
        </header>

        <nav aria-label={t("nav.label")} className="mb-6 flex gap-1 border-b border-slate-200">
          {(["home", "publish", "settings"] as const).map((p) => (
            <button
              key={p}
              type="button"
              aria-current={page === p ? "page" : undefined}
              onClick={() => setPage(p)}
              className={
                page === p
                  ? "-mb-px border-b-2 border-slate-900 px-3 py-2 text-sm font-medium"
                  : "px-3 py-2 text-sm text-slate-500 hover:text-slate-800"
              }
            >
              {t(`nav.${p}`)}
            </button>
          ))}
        </nav>

        {page === "home" ? (
          <HomePage />
        ) : page === "publish" ? (
          <PublishPage settings={settings} onOpenSettings={() => setPage("settings")} />
        ) : (
          <SettingsPage
            settings={settings}
            loadError={settingsError}
            onSettingsChange={setSettings}
            onLanguageChange={(lng) => void changeLanguage(lng)}
          />
        )}
      </main>
    </div>
  );
}
