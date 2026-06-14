"use client";

import Link from "next/link";
import type { GameListItem } from "@/lib/types";
import { useSlate } from "@/lib/slate";
import { formatGameTime, formatPct } from "@/lib/format";
import ProbabilityRing from "./ProbabilityRing";
import LabelBadge from "./LabelBadge";
import ParkBadge from "./ParkBadge";

/* Level 1 game card (PART 3 / Page 1). ~80px. Tap -> game detail. */
export default function GameCard({ game }: { game: GameListItem }) {
  const slate = useSlate();
  const time = formatGameTime(game.game_datetime);
  const pick = game.top_pick;

  return (
    <Link
      href={`/game/${game.game_id}`}
      className="flex items-center gap-3 rounded-lg border border-white/5 bg-card p-3 transition-colors hover:bg-hover"
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate font-sans font-semibold text-text-pri">
            {game.away_team}
            <span className="px-1.5 text-text-muted">@</span>
            {game.home_team}
          </span>
          <ParkBadge factor={game.park_factor} onlyExtremes />
        </div>
        <div className="mt-0.5 truncate font-mono text-[11px] text-text-muted">
          {game.away_sp} <span className="text-text-muted/60">vs</span> {game.home_sp}
        </div>
        {time && <div className="mt-0.5 font-mono text-[11px] text-text-muted">{time}</div>}
      </div>

      {pick ? (
        <div className="flex shrink-0 items-center gap-2">
          <div className="text-right">
            <div className="max-w-[8rem] truncate font-sans text-sm text-text-pri">{pick.name}</div>
            <div className="mt-1 flex items-center justify-end gap-1.5">
              <LabelBadge label={slate.labelFor(pick.p_per_pa)} size="sm" />
              <span className="font-mono text-[11px] text-text-muted">{formatPct(pick.p_per_pa)}</span>
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
