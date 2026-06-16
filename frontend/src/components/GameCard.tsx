"use client";

import Link from "next/link";
import type { GameListItem } from "@/lib/types";
import { useSlate } from "@/lib/slate";
import { formatGameTime } from "@/lib/format";
import { emblemsFor } from "@/lib/emblems";
import ProbabilityRing from "./ProbabilityRing";
import LabelBadge from "./LabelBadge";
import ParkBadge from "./ParkBadge";
import EmblemRow from "./EmblemBadge";

/* Level 1 game card (Page 1). Compact single-block row. Tap -> game detail. */
export default function GameCard({ game }: { game: GameListItem }) {
  const slate = useSlate();
  const time = formatGameTime(game.game_datetime);
  const pick = game.top_pick;
  // Only park is known at the slate-list level (teaser carries no hitter stats).
  const emblems = emblemsFor({ parkFactor: game.park_factor });

  return (
    <Link
      href={`/game/${game.game_id}`}
      className="flex items-center gap-3 rounded-lg border border-white/5 bg-card px-3 py-2 transition-colors hover:bg-hover"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-sans text-sm font-semibold text-text-pri">
            {game.away_team}
            <span className="px-1 text-text-muted">@</span>
            {game.home_team}
          </span>
          <ParkBadge factor={game.park_factor} onlyExtremes />
          <EmblemRow emblems={emblems} compact />
        </div>
        <div className="mt-0.5 truncate font-mono text-[11px] text-text-muted">
          {time && <span className="text-text-muted/90">{time} · </span>}
          {game.away_sp} <span className="text-text-muted/60">vs</span> {game.home_sp}
        </div>
      </div>

      {pick ? (
        <div className="flex shrink-0 items-center gap-2">
          <div className="text-right leading-tight">
            <div className="max-w-[7.5rem] truncate font-sans text-xs text-text-pri">{pick.name}</div>
            <div className="mt-0.5">
              <LabelBadge label={slate.labelFor(pick.p_per_pa)} size="sm" />
            </div>
          </div>
          <ProbabilityRing value={pick.p_per_pa} size={40} />
        </div>
      ) : (
        <span className="shrink-0 font-mono text-xs text-text-muted">Lineup TBD</span>
      )}
    </Link>
  );
}
