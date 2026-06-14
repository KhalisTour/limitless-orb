import type { StatKey } from "./types";

/*
  Formatting helpers (PART 5). Pure functions, no side effects.
*/

/** ISO UTC -> local-TZ "7:05 PM". null in -> null out (omit the row). */
export function formatGameTime(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  }).format(d);
}

/** Pretty date for headers, e.g. "Sat, Jun 14". null-safe. */
export function formatDate(dateStr: string | null): string | null {
  if (!dateStr) return null;
  // dateStr is "YYYY-MM-DD"; parse as local noon to avoid TZ rollback.
  const d = new Date(`${dateStr}T12:00:00`);
  if (Number.isNaN(d.getTime())) return dateStr;
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  }).format(d);
}

/** 0.0634 -> "6.3%". Handles the per-PA scale (already a fraction). */
export function formatPct(n: number, places = 1): string {
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(places)}%`;
}

/** Per-stat display formatting (PART 5). */
export function formatStat(stat: StatKey, val: number | null): string {
  if (val === null || val === undefined || !Number.isFinite(val)) return "—";
  switch (stat) {
    case "xslg":
      return val.toFixed(3);
    case "ev":
      return `${val.toFixed(1)} mph`;
    case "la":
      return `${val.toFixed(1)}°`;
    case "barrel_pct":
    case "hardhit_pct":
    case "whiff_pct":
    case "k_pct":
    case "bb_pct":
      return `${val.toFixed(1)}%`;
    default:
      return String(val);
  }
}

/** Short uppercase label for a stat key. */
export const STAT_LABEL: Record<StatKey, string> = {
  barrel_pct: "BARREL",
  hardhit_pct: "HARDHIT",
  xslg: "xSLG",
  ev: "EV",
  la: "LA",
  whiff_pct: "WHIFF",
  k_pct: "K%",
  bb_pct: "BB%",
};
