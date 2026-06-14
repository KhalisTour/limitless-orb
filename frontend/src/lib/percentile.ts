import type { Hitter, ProbLabel, StatKey } from "./types";
import { STAT_KEYS } from "./types";

/*
  Slate-relative thresholds (PART 2). Computed ONCE at the top level from
  /api/top-picks?n=200 and passed down via React context. Never hardcode.
*/

function quantile(sortedAsc: number[], q: number): number {
  if (sortedAsc.length === 0) return 0;
  const pos = (sortedAsc.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  if (sortedAsc[base + 1] !== undefined) {
    return sortedAsc[base] + rest * (sortedAsc[base + 1] - sortedAsc[base]);
  }
  return sortedAsc[base];
}

export interface ProbabilityLabels {
  thresholds: { elite: number; high: number; med: number };
  labelFor: (p: number) => ProbLabel;
}

export function computeProbabilityLabels(picks: Hitter[]): ProbabilityLabels {
  const ps = picks
    .map((h) => h.p_per_pa)
    .filter((p) => Number.isFinite(p))
    .sort((a, b) => a - b);

  const thresholds = {
    elite: quantile(ps, 0.9),
    high: quantile(ps, 0.75),
    med: quantile(ps, 0.5),
  };

  const labelFor = (p: number): ProbLabel => {
    if (p >= thresholds.elite) return "ELITE";
    if (p >= thresholds.high) return "HIGH";
    if (p >= thresholds.med) return "MED";
    return "LOW";
  };

  return { thresholds, labelFor };
}

export interface StatPercentiles {
  /** sorted-ascending values per stat, used for empirical percentile */
  thresholdsByStat: Record<StatKey, number[]>;
  /** Returns 0–100 percentile of val within the slate for that stat.
      null val -> null (omit dot). Some stats are "lower is better"
      (whiff_pct, k_pct) but the dot encodes raw distribution position;
      we keep it simple and rank by value ascending for all. */
  pctileFor: (stat: StatKey, val: number | null) => number | null;
}

export function computeStatPercentiles(picks: Hitter[]): StatPercentiles {
  const thresholdsByStat = {} as Record<StatKey, number[]>;
  for (const key of STAT_KEYS) {
    const vals = picks
      .map((h) => h.stats?.[key])
      .filter((v): v is number => v !== null && v !== undefined && Number.isFinite(v))
      .sort((a, b) => a - b);
    thresholdsByStat[key] = vals;
  }

  const pctileFor = (stat: StatKey, val: number | null): number | null => {
    if (val === null || val === undefined || !Number.isFinite(val)) return null;
    const arr = thresholdsByStat[stat];
    if (!arr || arr.length === 0) return null;
    // count values <= val
    let lo = 0;
    let hi = arr.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (arr[mid] <= val) lo = mid + 1;
      else hi = mid;
    }
    return Math.round((lo / arr.length) * 100);
  };

  return { thresholdsByStat, pctileFor };
}
