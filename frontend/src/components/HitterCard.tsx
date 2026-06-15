"use client";

import Link from "next/link";
import type { Hitter } from "@/lib/types";
import { useSlate } from "@/lib/slate";
import { formatGameTime, formatPct } from "@/lib/format";
import Headshot from "./Headshot";
import ProbabilityRing from "./ProbabilityRing";
import LabelBadge from "./LabelBadge";

/*
  HitterCard (PART 3 widget hierarchy).
    variant "game"    -> Level 2 card, 120px, lineup view
    variant "compact" -> horizontal row (top picks)
    variant "list"    -> alias of compact
  batter_id null -> disabled CTA + tooltip (error #5).
*/

interface Props {
  hitter: Hitter;
  variant: "list" | "game" | "compact";
  gameId: number;
  oppPitcher: string;
  gameDatetime?: string | null;
  rank?: number;
}

function matchupHref(gameId: number, batterId: string, oppPitcher: string) {
  return `/matchup/${gameId}/${batterId}/${encodeURIComponent(oppPitcher)}`;
}

export default function HitterCard({
  hitter,
  variant,
  gameId,
  oppPitcher,
  gameDatetime,
  rank,
}: Props) {
  const slate = useSlate();
  const label = slate.labelFor(hitter.p_per_pa);
  const disabled = hitter.batter_id === null;
  const href = disabled ? "#" : matchupHref(gameId, hitter.batter_id!, oppPitcher);

  if (variant === "game") {
    const inner = (
      <div className="flex items-center gap-3 rounded-lg border border-white/5 bg-card p-3 transition-colors hover:bg-hover">
        <Headshot batterId={hitter.batter_id} name={hitter.name} size={48} />
        <ProbabilityRing value={hitter.p_per_pa} size={60} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate font-sans font-semibold text-text-pri">{hitter.name}</span>
            <span className="font-mono text-xs text-text-muted">#{hitter.lineup_slot}</span>
          </div>
          <div className="mt-1 flex items-center gap-2">
            <LabelBadge label={label} size="sm" />
            <span className="font-mono text-xs text-text-muted">
              {formatPct(hitter.p_game_hr)} game HR
            </span>
          </div>
          <div className="mt-1.5 flex gap-3 font-mono text-[11px] text-text-muted">
            <span>BRL {hitter.stats?.barrel_pct != null ? `${hitter.stats.barrel_pct.toFixed(1)}%` : "—"}</span>
            <span>HH {hitter.stats?.hardhit_pct != null ? `${hitter.stats.hardhit_pct.toFixed(1)}%` : "—"}</span>
            <span>{hitter.exp_pa.toFixed(1)} PA</span>
          </div>
        </div>
      </div>
    );
    if (disabled) {
      return (
        <div className="relative cursor-not-allowed opacity-70" title="zone data not available yet">
          {inner}
        </div>
      );
    }
    return (
      <Link href={href} className="block">
        {inner}
      </Link>
    );
  }

  // compact / list — horizontal row
  const time = formatGameTime(gameDatetime ?? null);
  const inner = (
    <div className="flex items-center gap-3 rounded-lg border border-white/5 bg-card px-3 py-2.5 transition-colors hover:bg-hover">
      {rank !== undefined && (
        <span className="w-7 shrink-0 text-center font-mono text-2xl font-bold text-neon-lime">
          {rank}
        </span>
      )}
      <ProbabilityRing value={hitter.p_per_pa} size={40} />
      <div className="min-w-0 flex-1">
        <div className="truncate font-sans font-semibold text-text-pri">{hitter.name}</div>
        <div className="truncate font-mono text-[11px] text-text-muted">vs {oppPitcher}</div>
      </div>
      <LabelBadge label={label} size="sm" />
      {time && <span className="hidden shrink-0 font-mono text-[11px] text-text-muted sm:inline">{time}</span>}
    </div>
  );
  if (disabled) {
    return (
      <div className="cursor-not-allowed opacity-70" title="zone data not available yet">
        {inner}
      </div>
    );
  }
  return (
    <Link href={href} className="block">
      {inner}
    </Link>
  );
}
