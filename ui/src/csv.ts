import type { TFunction } from "i18next";

import type { Publication } from "./api";

/**
 * History as CSV that Excel opens correctly with a Russian locale: a UTF-8 BOM (so
 * Cyrillic is not garbled), ";" between columns (Excel's default where "," is the
 * decimal separator) and CRLF line ends.
 */
export const BOM = String.fromCharCode(0xfeff);

function cell(value: string): string {
  return /[";\r\n]/.test(value) ? `"${value.replace(/"/g, '""')}"` : value;
}

function localTime(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}`
  );
}

export function historyCsv(items: Publication[], t: TFunction): string {
  const header = [
    "created",
    "file",
    "title",
    "platform",
    "method",
    "target",
    "status",
    "scheduled",
    "url",
    "error",
  ].map((k) => t(`publications.csv.${k}`));
  const rows = items.map((p) => [
    localTime(p.created_at),
    p.video_path,
    p.title,
    t(`platform.${p.platform}`, { defaultValue: p.platform }),
    p.method === "account"
      ? t("settings.telegram.modeAccount")
      : p.method === "bot"
        ? t("settings.telegram.modeBot")
        : p.method,
    p.target ?? "",
    t(`publications.status.${p.status}`),
    localTime(p.scheduled_at),
    p.url ?? "",
    p.error_code
      ? t(`errors.${p.error_code}`, { ...p.error_params, defaultValue: t("errors.unknown") })
      : "",
  ]);
  return BOM + [header, ...rows].map((r) => r.map(cell).join(";")).join("\r\n") + "\r\n";
}

/** File name like `history-2026-11-07.csv` (local date). */
export function csvFileName(now = new Date()): string {
  return `history-${localTime(now.toISOString()).slice(0, 10)}.csv`;
}
