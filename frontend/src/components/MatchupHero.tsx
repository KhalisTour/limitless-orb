"use client";

import { useSlate } from "@/lib/slate";
import { formatPct } from "@/lib/format";
import ProbabilityRing from "./ProbabilityRing";
import LabelBadge from "./LabelBadge";

/* Matchup hero (PART 6 §1). Gradient placeholder seeded by batter_id hash —
   no real headshots in v1. */

function hashHue(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) % 360;
  return h;
}

export default function MatchupHero({
  batterId,
  name,
  pitcher,
  pPerPa,
  pGameHr,
}: {
  batterId: string;
  name: string;
  pitcher: string;
  pPerPa: number;
  pGameHr: number;
}) {
  const slate = useSlate();
  const hue = hashHue(batterId);
  const bg = `linear-gradient(135deg, hsl(${hue} 60% 18%), hsl(${(hue + 40) % 360} 55% 10%))`;

  return (
    <div
      className="relative overflow-hidden rounded-xl border border-white/5 p-5"
      style={{ background: bg }}
    >
      <div className="flex items-center gap-4">
        <ProbabilityRing value={pPerPa} size={100} />
        <div className="min-w-0">
          <h1 className="font-mono text-3xl font-bold leading-tight text-text-pri">{name}</h1>
          <p className="mt-0.5 font-sans text-sm text-text-muted">vs {pitcher}</p>
          <div className="mt-2 flex items-center gap-2">
            <LabelBadge label={slate.labelFor(pPerPa)} />
          </div>
          <div className="mt-2 flex gap-4 font-mono text-xs text-text-muted">
            <span>
              <span className="text-text-pri">{formatPct(pPerPa)}</span> per PA
            </span>
            <span>
              <span className="text-text-pri">{formatPct(pGameHr)}</span> game HR
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
