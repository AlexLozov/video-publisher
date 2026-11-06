import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { fetchHealth, fetchPlatforms, type Health, type PlatformInfo } from "../api";

type Status = { kind: "loading" } | { kind: "ok"; health: Health } | { kind: "error" };

export function HomePage() {
  const { t } = useTranslation();
  const [status, setStatus] = useState<Status>({ kind: "loading" });
  const [platforms, setPlatforms] = useState<PlatformInfo[]>([]);

  useEffect(() => {
    fetchHealth()
      .then((health) => setStatus({ kind: "ok", health }))
      .catch(() => setStatus({ kind: "error" }));
    fetchPlatforms()
      .then(setPlatforms)
      .catch(() => setPlatforms([]));
  }, []);

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-medium">{t("status.heading")}</h2>
        <BackendBadge status={status} />
      </div>

      <h3 className="mt-6 mb-3 text-sm font-medium uppercase tracking-wide text-slate-400">
        {t("platforms.heading")}
      </h3>
      {platforms.length === 0 ? (
        <p className="text-sm text-slate-400">{t("platforms.empty")}</p>
      ) : (
        <ul className="divide-y divide-slate-100">
          {platforms.map((p) => (
            <li key={p.id} className="flex items-center justify-between py-3">
              <span>{t(`platform.${p.id}`, { defaultValue: p.id })}</span>
              <span
                className={
                  p.available
                    ? "rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs text-emerald-700"
                    : "rounded-full bg-slate-100 px-2.5 py-0.5 text-xs text-slate-500"
                }
              >
                {p.available ? t("platforms.available") : t("platforms.inDevelopment")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function BackendBadge({ status }: { status: Status }) {
  const { t } = useTranslation();
  if (status.kind === "loading") {
    return <span className="text-sm text-slate-400">{t("status.checking")}</span>;
  }
  if (status.kind === "error") {
    return (
      <span className="flex items-center gap-2 text-sm text-rose-600">
        <span className="h-2 w-2 rounded-full bg-rose-500" />
        {t("status.down")}
      </span>
    );
  }
  return (
    <span className="flex items-center gap-2 text-sm text-emerald-700">
      <span className="h-2 w-2 rounded-full bg-emerald-500" />
      {t("status.ok", { version: status.health.version })}
    </span>
  );
}
