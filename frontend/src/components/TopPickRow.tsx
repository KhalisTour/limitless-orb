"use client";

import { useState } from "react";
import Link from "next/link";
import type { OddsLine, TopPick } from "@/lib/types";
import { useSlate } from "@/lib/slate";
import { formatGameTime, formatPct } from "@/lib/format";
import Headshot from "./Headshot";
import ProbabilityRing from "./ProbabilityRing";
import LabelBadge from "./LabelBadge";
import ModelViews from "./ModelViews";
import StatGrid from "./StatGrid";
import PlayerDistribution from "./PlayerDistribution";
import { EdgeBadge, EdgeDetail } from "./EdgeBadge";

/*
  Top Picks row with an inline mini-breakdown. Tap the row to expand: shows
  the per-player HR distribution, the three model views, and the full stat
  grid — without leaving the page. "Full matchup" link goes to the deep page.
*/

export default function TopPickRow({
  pick,
  odds,
  oddsBook = null,
}: {
  pick: TopPick;
  odds?: OddsLine;
  oddsBook?: string | null;
}) {
  const [open, setOpen] = useState(false);
  const slate = useSlate();
  const label = slate.labelFor(pick.p_per_pa);
  const time = formatGameTime(pick.game_datetime);
  const matchupHref =
    pick.batter_id !== null
      ? `/matchup/${pick.game_id}/${pick.batter_id}/${encodeURIComponent(pick.opp_pitcher)}`
      : null;

  return (
    <div className="overflow-hidden rounded-lg border border-white/5 bg-card">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-hover"
      >
        <span className="w-6 shrink-0 text-center font-mono text-xl font-bold text-neon-lime">
          {pick.rank}
        </span>
        <Headshot batterId={pick.batter_id} name={pick.name} size={40} />
        <ProbabilityRing value={pick.p_per_pa} size={40} />
        <div className="min-w-0 flex-1">
          <div className="truncate font-sans font-semibold text-text-pri">{pick.name}</div>
          <div className="truncate font-mono text-[11px] text-text-muted">vs {pick.opp_pitcher}</div>
        </div>
        <EdgeBadge line={odds} />
        <LabelBadge label={label} size="sm" />
        {time && <span className="hidden shrink-0 font-mono text-[11px] text-text-muted sm:inline">{time}</span>}
        <span
          className={`shrink-0 font-mono text-text-muted transition-transform ${open ? "rotate-90" : ""}`}
          aria-hidden
        >
          ›
        </span>
      </button>

      {open && (
        <div className="space-y-4 border-t border-white/5 px-3 py-4">
          <div className="flex flex-wrap gap-4 font-mono text-xs text-text-muted">
            <span>
              <span className="text-text-pri">{formatPct(pick.p_per_pa)}</span> per PA
            </span>
            <span>
              <span className="text-text-pri">{formatPct(pick.p_game_hr)}</span> game HR
            </span>
            <span>
              <span className="text-text-pri">{pick.exp_pa.toFixed(1)}</span> exp PA
            </span>
          </div>

          <EdgeDetail line={odds} book={oddsBook} />

          <div>
            <div className="mb-2 font-mono text-[11px] uppercase tracking-wide text-text-muted">
              Tonight&apos;s outcome odds
            </div>
            <PlayerDistribution pGameHr={pick.p_game_hr} pMultiHr={pick.p_multi_hr} />
          </div>

          <div>
            <div className="mb-2 font-mono text-[11px] uppercase tracking-wide text-text-muted">
              Model views
            </div>
            <ModelViews components={pick.components} ensemble={pick.p_per_pa} />
          </div>

          {pick.stats && <StatGrid stats={pick.stats} />}

          {matchupHref && (
            <Link
              href={matchupHref}
              className="inline-block rounded-lg border border-neon-lime/40 bg-neon-lime/10 px-3 py-1.5 font-mono text-xs font-bold text-neon-lime transition-colors hover:bg-neon-lime/20"
            >
              Full matchup →
            </Link>
          )}
        </div>
      )}
    </div>
  );
}
