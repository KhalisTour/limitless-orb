import { getTopPicks } from "@/lib/api";
import { formatDate, formatGameTimeET } from "@/lib/format";
import { abbrev } from "@/lib/teams";
import type { GameGroup, PoolHitter } from "@/lib/picks";
import PicksBoard from "@/components/PicksBoard";
import StaleBanner from "@/components/StaleBanner";
import OfflineShell from "@/components/OfflineShell";

export const revalidate = 300;

export default async function PlayPage() {
  const res = await getTopPicks(undefined, 200);
  if (!res.ok) return <OfflineShell />;

  const { picks, stale, date } = res.data;

  // Group pickable hitters (need a batter_id to grade) by game.
  type Acc = { away: string; home: string; datetime: string | null; hitters: PoolHitter[] };
  const byGame = new Map<number, Acc>();
  for (const p of picks) {
    if (p.batter_id === null) continue;
    let g = byGame.get(p.game_id);
    if (!g) {
      g = { away: p.away_team, home: p.home_team, datetime: p.game_datetime, hitters: [] };
      byGame.set(p.game_id, g);
    }
    g.hitters.push({
      batterId: p.batter_id,
      name: p.name,
      gameId: p.game_id,
      oppPitcher: p.opp_pitcher,
      pGameHr: p.p_game_hr,
      pPerPa: p.p_per_pa,
      gameDatetime: p.game_datetime,
    });
  }

  const games: GameGroup[] = [...byGame.entries()]
    .map(([gameId, g]) => ({
      gameId,
      label: `${abbrev(g.away)} @ ${abbrev(g.home)}`,
      timeET: formatGameTimeET(g.datetime),
      hitters: g.hitters.sort((a, b) => b.pPerPa - a.pPerPa), // Elite -> Low
    }))
    .sort((a, b) => {
      const ta = a.hitters[0]?.gameDatetime ?? "";
      const tb = b.hitters[0]?.gameDatetime ?? "";
      return ta < tb ? -1 : ta > tb ? 1 : 0;
    });

  return (
    <div>
      {stale && <StaleBanner date={date} />}
      <header className="mb-4">
        <h1 className="font-sans text-xl font-semibold text-text-pri">HR Pick&apos;em</h1>
        <p className="mt-0.5 font-mono text-xs text-text-muted">
          {formatDate(date) ?? date} · tap a game, pick up to 5 to homer
        </p>
      </header>
      <PicksBoard games={games} today={date} />
    </div>
  );
}
