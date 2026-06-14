"use client";

import { useState } from "react";
import type { GameDetail, Side } from "@/lib/types";
import { isSideError } from "@/lib/types";
import HitterCard from "./HitterCard";
import EmptySideCard from "./EmptySideCard";
import MonteCarloBar from "./MonteCarloBar";

/* Page 2 interactive body — Away/Home batter tabs + per-side sim. */
export default function GameView({ game }: { game: GameDetail }) {
  // Default to the first side that actually has a lineup, so an errored
  // away side doesn't greet the user with an empty card.
  const initialTab: "away" | "home" = isSideError(game.sides.away) && !isSideError(game.sides.home)
    ? "home"
    : "away";
  const [tab, setTab] = useState<"away" | "home">(initialTab);
  const side: Side = game.sides[tab];
  const sp = tab === "away" ? game.away_sp : game.home_sp;
  const team = tab === "away" ? game.away_team : game.home_team;

  return (
    <div>
      <div className="mb-4 flex gap-1.5">
        {(["away", "home"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`flex-1 rounded-lg border px-3 py-2 font-sans text-sm transition-colors ${
              tab === t
                ? "border-neon-lime/50 bg-neon-lime/10 text-neon-lime"
                : "border-white/10 text-text-muted hover:text-text-pri"
            }`}
          >
            {t === "away" ? game.away_team : game.home_team} Batters
          </button>
        ))}
      </div>

      {isSideError(side) ? (
        <EmptySideCard message={side.error} sp={sp} />
      ) : (
        <>
          <div className="space-y-2.5">
            {[...side.hitters]
              .sort((a, b) => a.lineup_slot - b.lineup_slot)
              .map((h) => (
                <HitterCard
                  key={`${h.batter_id ?? h.name}`}
                  hitter={h}
                  variant="game"
                  gameId={game.game_id}
                  oppPitcher={side.pitcher}
                  gameDatetime={game.game_datetime}
                />
              ))}
          </div>

          <div className="mt-5 space-y-2">
            <MonteCarloBar dist={side.sim.team_hr_dist} />
            <p className="px-1 font-mono text-xs text-text-muted">
              {team} back-to-back HR:{" "}
              <span className="text-neon-cyan">
                {(side.sim.back_to_back_per_game * 100).toFixed(1)}%
              </span>
            </p>
          </div>
        </>
      )}
    </div>
  );
}
