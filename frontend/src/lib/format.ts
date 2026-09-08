import type { StatKey } from "./types";

/*
  Formatting helpers (PART 5). Pure functions, no side effects.
*/

/** ISO UTC -> Eastern "1:05 PM ET". null in -> null out (omit the row).
 *
 * Pinned to America/New_York on purpose. This used to pass `undefined` as the
 * locale, which resolves to whatever timezone the code happens to run in — and
 * these times are rendered during SSR, so that is the SERVER's timezone, not
 * the viewer's. On a UTC host every visitor saw a 1:05 PM ET first pitch
 * printed as "5:05 PM", with no timezone label to give it away, while the
 * Pick'em board (which already pinned Eastern) showed "1:05p ET" for the same
 * game. MLB publishes schedules in Eastern, so Eastern is the one answer that
 * is correct for every viewer and identical on server and client.
 */
export function formatGameTime(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return `${new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "America/New_York",
  }).format(d)} ET`;
}

/** ISO UTC -> compact Eastern time, e.g. "6:45p ET". null-safe. */
export function formatGameTimeET(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "America/New_York",
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const period = get("dayPeriod").toLowerCase().startsWith("p") ? "p" : "a";
  return `${get("hour")}:${get("minute")}${period} ET`;
}

/** Pretty date for headers, e.g. "Sat, Jun 14". null-safe. */
export function formatDate(dateStr: string | null): string | null {
  if (!dateStr) return null;
  // dateStr is a slate date ("YYYY-MM-DD"), not an instant. Parse it as UTC noon
  // and format in UTC so it renders as the same calendar day everywhere —
  // formatting a bare date in an ambient timezone is how a slate labelled
  // 2026-09-07 shows up as "Sep 6" for anyone west of the host.
  const d = new Date(`${dateStr}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return dateStr;
  return new Intl.DateTimeFormat("en-US", {
    weekday: "short",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
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
