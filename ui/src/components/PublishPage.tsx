import { useCallback, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, startPublication, type Settings } from "../api";
import { buildCaption, fileName, parseHashtags, TELEGRAM_CAPTION_LIMIT } from "../caption";
import { useDesktopApi, useFileDrop } from "../desktop";
import { errorKey } from "../errors";
import { PublicationList } from "./PublicationList";

interface Props {
  settings: Settings | null;
  onOpenSettings: () => void;
  pollMs?: number;
}

export function PublishPage({ settings, onOpenSettings, pollMs }: Props) {
  const { t } = useTranslation();
  const desktop = useDesktopApi();
  const ids = { path: useId(), title: useId(), description: useId(), tags: useId() };

  const [videoPath, setVideoPath] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [hashtags, setHashtags] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ key: string; params: Record<string, unknown> } | null>(
    null,
  );
  const [refreshKey, setRefreshKey] = useState(0);

  const onDrop = useCallback((path: string) => {
    setVideoPath(path);
    setError(null);
  }, []);
  useFileDrop(onDrop);

  const tags = parseHashtags(hashtags);
  const captionLength = buildCaption(title, description, tags).length;
  const captionTooLong = captionLength > TELEGRAM_CAPTION_LIMIT;

  const tg = settings?.telegram;
  const configured =
    tg !== undefined &&
    tg.mode === "bot" &&
    Boolean(tg.channel) &&
    Boolean(settings?.secrets.telegram_bot_token);

  async function pick() {
    if (!desktop) return;
    const path = await desktop.pick_video();
    if (path) {
      setVideoPath(path);
      setError(null);
    }
  }

  async function publish() {
    setBusy(true);
    setError(null);
    try {
      await startPublication({
        video_path: videoPath.trim(),
        platform: "telegram",
        title,
        description,
        hashtags: tags,
      });
      setVideoPath("");
      setTitle("");
      setDescription("");
      setHashtags("");
      setRefreshKey((k) => k + 1);
    } catch (err) {
      setError({ key: errorKey(err), params: err instanceof ApiError ? err.params : {} });
    } finally {
      setBusy(false);
    }
  }

  const input =
    "w-full rounded-lg border border-slate-200 px-3 py-1.5 text-sm outline-none focus:border-slate-400";

  return (
    <div className="space-y-6">
      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="mb-4 text-lg font-medium">{t("publish.title")}</h2>

        {/* Video */}
        <div className="space-y-2">
          <span className="text-sm font-medium">{t("publish.video")}</span>
          {desktop ? (
            <div
              data-testid="dropzone"
              className="flex flex-col items-center gap-2 rounded-xl border-2 border-dashed border-slate-200 p-5 text-center"
            >
              {videoPath ? (
                <>
                  <span className="text-sm font-medium">{fileName(videoPath)}</span>
                  <span data-testid="video-path" className="break-all text-xs text-slate-400">
                    {videoPath}
                  </span>
                </>
              ) : (
                <span className="text-sm text-slate-500">{t("publish.dropHint")}</span>
              )}
              <button
                type="button"
                onClick={() => void pick()}
                className="rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm"
              >
                {videoPath ? t("publish.change") : t("publish.pick")}
              </button>
            </div>
          ) : (
            <>
              <input
                id={ids.path}
                aria-label={t("publish.pathLabel")}
                value={videoPath}
                onChange={(e) => setVideoPath(e.target.value)}
                placeholder={"C:\\Videos\\clip.mp4"}
                className={input}
              />
              <p className="text-xs text-slate-400">{t("publish.pathHint")}</p>
            </>
          )}
          <p className="text-xs text-slate-400">{t("publish.formatHint")}</p>
        </div>

        {/* Text */}
        <div className="mt-5 space-y-1.5">
          <label htmlFor={ids.title} className="text-sm font-medium">
            {t("publish.titleLabel")}
          </label>
          <input
            id={ids.title}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className={input}
          />
        </div>
        <div className="mt-4 space-y-1.5">
          <label htmlFor={ids.description} className="text-sm font-medium">
            {t("publish.descriptionLabel")}
          </label>
          <textarea
            id={ids.description}
            value={description}
            rows={4}
            onChange={(e) => setDescription(e.target.value)}
            className={input}
          />
        </div>
        <div className="mt-4 space-y-1.5">
          <label htmlFor={ids.tags} className="text-sm font-medium">
            {t("publish.hashtagsLabel")}
          </label>
          <input
            id={ids.tags}
            value={hashtags}
            onChange={(e) => setHashtags(e.target.value)}
            placeholder="#travel #shorts"
            className={input}
          />
        </div>
        <p
          data-testid="caption-counter"
          className={`mt-2 text-xs ${captionTooLong ? "text-rose-600" : "text-slate-400"}`}
        >
          {t("publish.captionCounter", { count: captionLength, max: TELEGRAM_CAPTION_LIMIT })}
        </p>

        {/* Destination */}
        <div className="mt-5 rounded-xl bg-slate-50 p-3 text-sm">
          <span className="font-medium">{t("publish.destination")}: </span>
          {configured && tg ? (
            <span data-testid="destination">
              {t("platform.telegram")} · {t("settings.telegram.modeBot")} · {tg.channel}
            </span>
          ) : (
            <span className="text-slate-500">
              {t("publish.notConfigured")}{" "}
              <button type="button" onClick={onOpenSettings} className="text-sky-700 underline">
                {t("publish.openSettings")}
              </button>
            </span>
          )}
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={busy || videoPath.trim() === "" || captionTooLong}
            onClick={() => void publish()}
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-40"
          >
            {busy ? t("publish.starting") : t("publish.publishNow")}
          </button>
          {error && (
            <span role="alert" className="text-sm text-rose-600">
              {t(error.key, { ...error.params, defaultValue: t("errors.unknown") })}
            </span>
          )}
        </div>
      </section>

      <PublicationList refreshKey={refreshKey} pollMs={pollMs} />
    </div>
  );
}
