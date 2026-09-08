import type { ProbLabel, SlateContext, StatKey } from "./types";

/*
  Slate-relative thresholds (PART 2).

  These used to be computed in the browser from /api/top-picks?n=200, which was
  wrong three ways at once — the endpoint caps n at 200 while a slate carries
  235-260 hitters, it gates unposted lineups out of the population, and it cost
  ~254 KB per page render to derive a dozen numbers. The backend now computes
  them over the whole ungated slate at /api/slate-context and this file just
  reads that payload.
*/

export interface ProbabilityLabels {
  thresholds: { elite: number; high: number; med: number };
  labelFor: (p: number) => ProbLabel;
}

/** Historical fallback used when the slate context is unavailable. */
export const FALLBACK_THRESHOLDS = { elite: 0.0502, high: 0.0406, med: 0.0298 };

export function labelsFrom(
  thresholds: { elite: number; high: number; med: number },
): ProbabilityLabels {
  const labelFor = (p: number): ProbLabel => {
    if (!Number.isFinite(p)) return "LOW";
    if (p >= thresholds.elite) return "ELITE";
    if (p >= thresholds.high) return "HIGH";
    if (p >= thresholds.med) return "MED";
    return "LOW";
  };
  return { thresholds, labelFor };
}

/**
 * Percentile of `val` against the slate, read off the 101-point curve the API
 * returns (index i is the i-th percentile). Counting how many breakpoints the
 * value clears IS its percentile, so no interpolation is needed.
 */
export function pctileFromCurves(
  curves: Record<string, number[]> | undefined,
): (stat: StatKey, val: number | null) => number | null {
  return (stat, val) => {
    if (val === null || val === undefined || !Number.isFinite(val)) return null;
    const curve = curves?.[stat];
    if (!curve || curve.length === 0) return null;
    let lo = 0;
    let hi = curve.length;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (curve[mid] <= val) lo = mid + 1;
      else hi = mid;
    }
    return Math.round((lo / curve.length) * 100);
  };
}

export function thresholdsFrom(ctx: SlateContext | null): {
  elite: number;
  high: number;
  med: number;
} {
  const t = ctx?.thresholds;
  if (!t || !Number.isFinite(t.elite) || !Number.isFinite(t.high) || !Number.isFinite(t.med)) {
    return FALLBACK_THRESHOLDS;
  }
  return { elite: t.elite, high: t.high, med: t.med };
}
