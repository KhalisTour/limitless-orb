"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { ProbLabel, SlateContext, StatKey } from "./types";
import {
  FALLBACK_THRESHOLDS,
  labelsFrom,
  pctileFromCurves,
  thresholdsFrom,
} from "./percentile";

/*
  Slate context (PART 2 / PART 5). Holds the ELITE/HIGH/MED/LOW thresholds and
  the per-stat percentile curves, both computed server-side over the whole slate
  by /api/slate-context and passed in once from the root layout.
*/

interface SlateValue {
  thresholds: { elite: number; high: number; med: number };
  labelFor: (p: number) => ProbLabel;
  pctileFor: (stat: StatKey, val: number | null) => number | null;
}

const SlateContextReact = createContext<SlateValue | null>(null);

export function SlateProvider({
  context,
  children,
}: {
  context: SlateContext | null;
  children: ReactNode;
}) {
  const value = useMemo<SlateValue>(() => {
    // No slate context (API down, or a date with no predictions) -> sane
    // historical fallback, so we never label a whole board ELITE off zeroes.
    if (!context || !context.n_hitters) return FALLBACK;
    return {
      thresholds: thresholdsFrom(context),
      labelFor: labelsFrom(thresholdsFrom(context)).labelFor,
      pctileFor: pctileFromCurves(context.stat_percentiles),
    };
  }, [context]);

  return (
    <SlateContextReact.Provider value={value}>{children}</SlateContextReact.Provider>
  );
}

/* Safe fallback so components never crash if used outside a provider. */
const FALLBACK: SlateValue = {
  thresholds: FALLBACK_THRESHOLDS,
  labelFor: labelsFrom(FALLBACK_THRESHOLDS).labelFor,
  pctileFor: () => null,
};

export function useSlate(): SlateValue {
  return useContext(SlateContextReact) ?? FALLBACK;
}
