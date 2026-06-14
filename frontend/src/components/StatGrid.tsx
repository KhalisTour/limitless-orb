"use client";

import type { HitterStats, StatKey } from "@/lib/types";
import { STAT_KEYS } from "@/lib/types";
import { useSlate } from "@/lib/slate";
import { formatStat, STAT_LABEL } from "@/lib/format";

/*
  StatGrid (PART 3). 8 stats, percentile dot above each value. Dots encode
  silently (no emoji, no text labels). Hidden entirely if stats null OR
  every field null (error #6) — never rows of em-dashes.
*/

function dotColor(pctile: number | null): string | null {
  if (pctile === null) return null; // omit dot
  if (pctile >= 80) return "#ff2d6f"; // neon-hot
  if (pctile >= 50) return "#ffa726"; // neon-amber
  if (pctile >= 20) return "rgba(0,229,255,0.5)"; // neon-cyan 50%
  return "#444444";
}

export default function StatGrid({ stats }: { stats: HitterStats | null }) {
  const slate = useSlate();

  if (!stats) return null;
  const allNull = STAT_KEYS.every((k) => stats[k] === null || stats[k] === undefined);
  if (allNull) return null;

  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-white/5 bg-white/5 sm:grid-cols-4">
      {STAT_KEYS.map((key: StatKey) => {
        const val = stats[key];
        const pctile = slate.pctileFor(key, val ?? null);
        const dc = dotColor(pctile);
        return (
          <div key={key} className="flex flex-col items-center gap-1 bg-card px-2 py-3">
            <span
              className="h-2 w-2 rounded-full"
              style={dc ? { backgroundColor: dc } : { backgroundColor: "transparent" }}
            />
            <span className="font-mono text-lg font-bold text-text-pri">
              {formatStat(key, val ?? null)}
            </span>
            <span className="font-mono text-[11px] uppercase text-text-muted">
              {STAT_LABEL[key]}
            </span>
          </div>
        );
      })}
    </div>
  );
}
