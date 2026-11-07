import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ApiError,
  clearHistory,
  deletePublication,
  fetchPublications,
  type Publication,
} from "../api";
import { fileName } from "../caption";
import { csvFileName, historyCsv } from "../csv";
import { useDesktopApi } from "../desktop";
import { errorKey } from "../errors";

const STATUS_STYLE: Record<string, string> = {
  publishing: "bg-amber-50 text-amber-700",
  published: "bg-emerald-50 text-emerald-700",
  failed: "bg-rose-50 text-rose-700",
  scheduled: "bg-sky-50 text-sky-700",
  draft: "bg-slate-100 text-slate-500",
};

/** How many records the CSV export includes at most (the backend's limit). */
const EXPORT_LIMIT = 1000;

interface Props {
  /** Change it to reload the list (e.g. after starting a publication). */
  refreshKey: number;
  pollMs?: number;
}

type Notice = { kind: "ok" | "error"; text: string } | null;

/** Upload cards (one per video, all its networks inside), newest first. */
function groupByBatch(items: Publication[]): Publication[][] {
  const groups = new Map<string, Publication[]>();
  for (const p of items) {
    const key = p.batch_id || p.id;
    const group = groups.get(key);
    if (group) group.push(p);
    else groups.set(key, [p]);
  }
  return [...groups.values()];
}

/** Recent publications; keeps polling while any of them is still uploading. */
export function PublicationList({ refreshKey, pollMs = 1500 }: Props) {
  const { t, i18n } = useTranslation();
  const desktop = useDesktopApi();
  const [items, setItems] = useState<Publication[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [reload, setReload] = useState(0);
  const [confirmClear, setConfirmClear] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const load = async () => {
      try {
        const list = await fetchPublications();
        if (cancelled) return;
        setItems(list);
        setLoadFailed(false);
        if (list.some((p) => p.status === "publishing")) {
          timer = setTimeout(() => void load(), pollMs);
        }
      } catch {
        if (cancelled) return;
        setLoadFailed(true);
        timer = setTimeout(() => void load(), pollMs * 2);
      }
    };
    void load();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [refreshKey, pollMs, reload]);

  const fmt = (iso: string) =>
    new Date(iso).toLocaleString(i18n.resolvedLanguage, { dateStyle: "medium", timeStyle: "short" });
  const apiErrorText = (err: unknown) =>
    t(errorKey(err), {
      ...(err instanceof ApiError ? err.params : {}),
      defaultValue: t("errors.unknown"),
    });

  async function exportCsv() {
    setNotice(null);
    try {
      const all = await fetchPublications(EXPORT_LIMIT);
      const content = historyCsv(all, t);
      const name = csvFileName();
      if (desktop?.save_csv) {
        const saved = await desktop.save_csv(name, content);
        if (saved) setNotice({ kind: "ok", text: `${t("publications.exported")}: ${saved}` });
      } else {
        // Browser (dev mode): a regular download.
        const url = URL.createObjectURL(new Blob([content], { type: "text/csv;charset=utf-8" }));
        const a = document.createElement("a");
        a.href = url;
        a.download = name;
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch {
      setNotice({ kind: "error", text: t("publications.exportFailed") });
    }
  }

  async function clearAll() {
    setConfirmClear(false);
    setNotice(null);
    try {
      const { deleted } = await clearHistory();
      setNotice({ kind: "ok", text: t("publications.cleared", { count: deleted }) });
    } catch (err) {
      setNotice({ kind: "error", text: apiErrorText(err) });
    }
    setReload((n) => n + 1);
  }

  async function deleteCard(group: Publication[]) {
    setNotice(null);
    try {
      for (const p of group) await deletePublication(p.id);
    } catch (err) {
      setNotice({ kind: "error", text: apiErrorText(err) });
    }
    setReload((n) => n + 1);
  }

  const button =
    "rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm disabled:opacity-40";
  const hasItems = items !== null && items.length > 0;

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-lg font-medium">{t("publications.title")}</h2>
        {hasItems && !confirmClear && (
          <>
            <button type="button" onClick={() => void exportCsv()} className={button}>
              {t("publications.exportCsv")}
            </button>
            <button type="button" onClick={() => setConfirmClear(true)} className={button}>
              {t("publications.clear")}
            </button>
          </>
        )}
        {confirmClear && (
          <div role="group" className="flex flex-wrap items-center gap-2 text-sm">
            <span>{t("publications.confirmClear")}</span>
            <button
              type="button"
              onClick={() => void clearAll()}
              className="rounded-lg bg-rose-600 px-3 py-1.5 text-white"
            >
              {t("publications.yes")}
            </button>
            <button type="button" onClick={() => setConfirmClear(false)} className={button}>
              {t("publications.no")}
            </button>
          </div>
        )}
      </div>

      {notice && (
        <p
          role={notice.kind === "error" ? "alert" : "status"}
          className={
            "mb-3 break-all text-sm " +
            (notice.kind === "error" ? "text-rose-600" : "text-emerald-700")
          }
        >
          {notice.text}
        </p>
      )}
      {loadFailed && (
        <p role="alert" className="mb-3 text-sm text-rose-600">
          {t("publications.loadFailed")}
        </p>
      )}
      {items === null ? (
        !loadFailed && <p className="text-sm text-slate-400">{t("common.loading")}</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-slate-400">{t("publications.empty")}</p>
      ) : (
        <ul className="space-y-3">
          {groupByBatch(items).map((group) => (
            <HistoryCard
              key={group[0].batch_id || group[0].id}
              group={group}
              fmt={fmt}
              onDelete={() => deleteCard(group)}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function HistoryCard({
  group,
  fmt,
  onDelete,
}: {
  group: Publication[];
  fmt: (iso: string) => string;
  onDelete: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const first = group[0];
  const running = group.some((p) => p.status === "publishing");
  const hasScheduled = group.some((p) => p.status === "scheduled");

  async function remove() {
    setBusy(true);
    await onDelete();
    setBusy(false);
    setConfirm(false);
  }

  return (
    <li data-testid="history-card" className="rounded-xl border border-slate-200 p-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium" title={first.video_path}>
            {first.title || fileName(first.video_path)}
          </p>
          <p className="truncate text-xs text-slate-400" title={first.video_path}>
            {t("publications.created", { date: fmt(first.created_at) })}
            {first.title ? ` · ${fileName(first.video_path)}` : ""}
          </p>
        </div>
        {!confirm && (
          <button
            type="button"
            disabled={running}
            onClick={() => setConfirm(true)}
            aria-label={t("publications.delete")}
            title={t("publications.delete")}
            className="shrink-0 rounded-lg px-2 py-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600 disabled:opacity-30"
          >
            <TrashIcon />
          </button>
        )}
      </div>

      {confirm && (
        <div role="group" className="mt-3 rounded-lg bg-rose-50 p-3 text-sm">
          <p>{t("publications.confirmDelete")}</p>
          {hasScheduled && (
            <p className="text-xs text-rose-700">{t("publications.deleteScheduledHint")}</p>
          )}
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => void remove()}
              className="rounded-lg bg-rose-600 px-3 py-1 text-white disabled:opacity-40"
            >
              {t("publications.yes")}
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirm(false)}
              className="rounded-lg border border-slate-200 bg-white px-3 py-1"
            >
              {t("publications.no")}
            </button>
          </div>
        </div>
      )}

      <ul className="mt-3 space-y-2">
        {group.map((p) => (
          <li key={p.id} data-testid="publication" className="rounded-lg bg-slate-50 px-3 py-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-medium">
                {t(`platform.${p.platform}`, { defaultValue: p.platform })}
              </span>
              {p.target && <span className="text-xs text-slate-500">{p.target}</span>}
              <span
                data-testid="publication-status"
                className={`ml-auto shrink-0 rounded-full px-2.5 py-0.5 text-xs ${STATUS_STYLE[p.status] ?? ""}`}
              >
                {t(`publications.status.${p.status}`)}
              </span>
            </div>
            {p.status === "scheduled" && p.scheduled_at && (
              <p data-testid="publication-scheduled" className="mt-1 text-sm text-sky-700">
                {t("publications.scheduledFor", { date: fmt(p.scheduled_at) })}
              </p>
            )}
            {p.status === "published" && p.url && (
              <a
                href={p.url}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-block text-sm text-sky-700 underline"
              >
                {t("publications.openPost")}
              </a>
            )}
            {p.status === "failed" && p.error_code && (
              <p className="mt-1 text-sm text-rose-600">
                {t(`errors.${p.error_code}`, {
                  ...p.error_params,
                  defaultValue: t("errors.unknown"),
                })}
              </p>
            )}
          </li>
        ))}
      </ul>
    </li>
  );
}

function TrashIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
      <path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v6M14 11v6" />
    </svg>
  );
}
