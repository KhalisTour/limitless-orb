"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { Hitter, ProbLabel, StatKey } from "./types";
import { computeProbabilityLabels, computeStatPercentiles } from "./percentile";

/*
  Slate context (PART 2 / PART 5). Holds the ELITE/HIGH/MED/LOW thresholds
  and per-stat percentile data, computed ONCE from /api/top-picks?n=200 at
  the top level and passed down. Next.js context + SWR is the whole state
  surface — no Redux/Zustand.

  We pass the raw `picks` array (serializable) from the server into this
  client provider and memoize the derived functions here.
*/

interface SlateValue {
  thresholds: { elite: number; high: number; med: number };
  labelFor: (p: number) => ProbLabel;
  pctileFor: (stat: StatKey, val: number | null) => number | null;
}

const SlateContext = createContext<SlateValue | null>(null);

export function SlateProvider({
  picks,
  children,
}: {
  picks: Hitter[];
  children: ReactNode;
}) {
  const value = useMemo<SlateValue>(() => {
    // Empty slate (API down / no picks yet) -> sane historical fallback so
    // we don't label everything ELITE off zero thresholds.
    if (picks.length === 0) return FALLBACK;
    const probs = computeProbabilityLabels(picks);
    const stats = computeStatPercentiles(picks);
    return {
      thresholds: probs.thresholds,
      labelFor: probs.labelFor,
      pctileFor: stats.pctileFor,
    };
  }, [picks]);

  return <SlateContext.Provider value={value}>{children}</SlateContext.Provider>;
}

/* Safe fallback so components never crash if used outside a provider. */
const FALLBACK: SlateValue = {
  thresholds: { elite: 0.0502, high: 0.0406, med: 0.0298 },
  labelFor: (p) =>
    p >= 0.0502 ? "ELITE" : p >= 0.0406 ? "HIGH" : p >= 0.0298 ? "MED" : "LOW",
  pctileFor: () => null,
};

export function useSlate(): SlateValue {
  return useContext(SlateContext) ?? FALLBACK;
}
