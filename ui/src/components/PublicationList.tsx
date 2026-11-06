import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchPublications, type Publication } from "../api";
import { fileName } from "../caption";

const STATUS_STYLE: Record<string, string> = {
  publishing: "bg-amber-50 text-amber-700",
  published: "bg-emerald-50 text-emerald-700",
  failed: "bg-rose-50 text-rose-700",
  scheduled: "bg-sky-50 text-sky-700",
  draft: "bg-slate-100 text-slate-500",
};

interface Props {
  /** Change it to reload the list (e.g. after starting a publication). */
  refreshKey: number;
  pollMs?: number;
}

/** Recent publications; keeps polling while any of them is still uploading. */
export function PublicationList({ refreshKey, pollMs = 1500 }: Props) {
  const { t } = useTranslation();
  const [items, setItems] = useState<Publication[] | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);

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
  }, [refreshKey, pollMs]);

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="mb-4 text-lg font-medium">{t("publications.title")}</h2>
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
        <ul className="divide-y divide-slate-100">
          {items.map((p) => (
            <li key={p.id} data-testid="publication" className="py-3">
              <div className="flex items-center justify-between gap-3">
                <span className="min-w-0 truncate text-sm font-medium" title={p.video_path}>
                  {p.title || fileName(p.video_path)}
                </span>
                <span
                  data-testid="publication-status"
                  className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs ${STATUS_STYLE[p.status] ?? ""}`}
                >
                  {t(`publications.status.${p.status}`)}
                </span>
              </div>
              <div className="mt-1 text-xs text-slate-500">
                {t(`platform.${p.platform}`, { defaultValue: p.platform })}
                {p.target ? ` · ${p.target}` : ""}
              </div>
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
      )}
    </section>
  );
}
