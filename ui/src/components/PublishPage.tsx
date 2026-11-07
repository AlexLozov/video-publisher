import { useCallback, useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, startBatch, type Settings } from "../api";
import { ALL_PLATFORMS, isConfigured, SUPPORTED_PLATFORMS, type PlatformId } from "../platforms";
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
  const ids = {
    path: useId(),
    title: useId(),
    description: useId(),
    tags: useId(),
    when: useId(),
  };

  const [videoPath, setVideoPath] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [hashtags, setHashtags] = useState("");
  const [later, setLater] = useState(false);
  /** Value of the datetime-local input: local time without offset. */
  const [scheduleAt, setScheduleAt] = useState("");
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
  const accountMode = tg?.mode === "account";
  const configuredList = SUPPORTED_PLATFORMS.filter((p) => isConfigured(settings, p));
  const configuredKey = configuredList.join(",");
  // Every set-up network is ticked by default; re-tick when settings change.
  const [selected, setSelected] = useState<PlatformId[]>(configuredList);
  useEffect(() => {
    setSelected(configuredKey ? (configuredKey.split(",") as PlatformId[]) : []);
  }, [configuredKey]);
  const toggle = (p: PlatformId) =>
    setSelected((cur) => (cur.includes(p) ? cur.filter((x) => x !== p) : [...cur, p]));
  const telegramOn = selected.includes("telegram");
  // Only "my account" lets Telegram keep a scheduled post (the app may be off then).
  const canSchedule = accountMode && telegramOn;
  const scheduling = canSchedule && later;

  async function pick() {
    if (!desktop) return;
    try {
      const path = await desktop.pick_video();
      if (path) {
        setVideoPath(path);
        setError(null);
      }
    } catch {
      // A failing native dialog used to do nothing at all; tell the user instead.
      setError({ key: "errors.file_dialog_failed", params: {} });
    }
  }

  async function publish() {
    setBusy(true);
    setError(null);
    try {
      await startBatch({
        video_path: videoPath.trim(),
        platforms: ALL_PLATFORMS.filter((p) => selected.includes(p)),
        title,
        description,
        hashtags: tags,
        // datetime-local has no offset; Date reads it as local time, ISO adds "Z".
        ...(scheduling ? { scheduled_at: new Date(scheduleAt).toISOString() } : {}),
      });
      setVideoPath("");
      setLater(false);
      setScheduleAt("");
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
          <p className="text-xs text-slate-400">
            {accountMode ? t("publish.formatHintAccount") : t("publish.formatHintBot")}
          </p>
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

        {/* Networks: every set-up one is ticked by default */}
        <fieldset className="mt-5 space-y-1 rounded-xl bg-slate-50 p-3 text-sm">
          <legend className="sr-only">{t("publish.destination")}</legend>
          <p aria-hidden className="mb-1 font-medium">
            {t("publish.destination")}
          </p>
          {ALL_PLATFORMS.map((p) => {
            const supported = SUPPORTED_PLATFORMS.includes(p);
            const ready = isConfigured(settings, p);
            return (
              <div key={p} className="flex flex-wrap items-center gap-2 py-0.5">
                <label
                  className={
                    "flex items-center gap-2 " + (ready ? "cursor-pointer" : "text-slate-400")
                  }
                >
                  <input
                    type="checkbox"
                    data-testid={`network-${p}`}
                    checked={selected.includes(p)}
                    disabled={!ready}
                    onChange={() => toggle(p)}
                  />
                  {p === "telegram" && ready && tg ? (
                    <span data-testid="destination">
                      {t("platform.telegram")} ·{" "}
                      {accountMode
                        ? t("settings.telegram.modeAccount")
                        : t("settings.telegram.modeBot")}{" "}
                      · {tg.channel}
                    </span>
                  ) : (
                    <span>{t(`platform.${p}`)}</span>
                  )}
                </label>
                {!supported && (
                  <span className="rounded-full bg-slate-100 px-2 text-xs text-slate-500">
                    {t("settings.state.soon")}
                  </span>
                )}
                {supported && !ready && (
                  <span className="text-slate-500">
                    {p === "telegram" &&
                      (accountMode
                        ? t("publish.notConfiguredAccount")
                        : t("publish.notConfiguredBot"))}{" "}
                    <button
                      type="button"
                      onClick={onOpenSettings}
                      className="text-sky-700 underline"
                    >
                      {t("publish.openSettings")}
                    </button>
                  </span>
                )}
              </div>
            );
          })}
        </fieldset>

        {/* When (only "my account": Telegram keeps the scheduled post) */}
        {canSchedule && (
          <fieldset className="mt-5 space-y-2">
            <legend className="mb-1 text-sm font-medium">{t("publish.when")}</legend>
            <div className="flex gap-4 text-sm">
              <label className="flex items-center gap-1.5">
                <input
                  type="radio"
                  name="publish-when"
                  checked={!later}
                  onChange={() => setLater(false)}
                />
                {t("publish.now")}
              </label>
              <label className="flex items-center gap-1.5">
                <input
                  type="radio"
                  name="publish-when"
                  checked={later}
                  onChange={() => setLater(true)}
                />
                {t("publish.later")}
              </label>
            </div>
            {later && (
              <div className="space-y-1.5">
                <label htmlFor={ids.when} className="text-sm">
                  {t("publish.scheduleAt")}
                </label>
                <input
                  id={ids.when}
                  type="datetime-local"
                  value={scheduleAt}
                  onChange={(e) => setScheduleAt(e.target.value)}
                  className={input}
                />
                <p className="text-xs text-slate-400">{t("publish.scheduleHint")}</p>
              </div>
            )}
          </fieldset>
        )}

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={
              busy ||
              videoPath.trim() === "" ||
              selected.length === 0 ||
              captionTooLong ||
              (scheduling && scheduleAt === "")
            }
            onClick={() => void publish()}
            className="rounded-lg bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-40"
          >
            {busy
              ? t("publish.starting")
              : scheduling
                ? t("publish.schedule")
                : t("publish.publishNow")}
          </button>
          {error && (
            <span role="alert" className="text-sm text-rose-600">
              {typeof error.params.platform === "string" &&
                `${t(`platform.${error.params.platform}`)}: `}
              {t(error.key, { ...error.params, defaultValue: t("errors.unknown") })}
            </span>
          )}
        </div>
      </section>

      <PublicationList refreshKey={refreshKey} pollMs={pollMs} />
    </div>
  );
}
