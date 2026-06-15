import { getTopPicks } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { Pick } from "@/lib/picks";
import PicksBoard from "@/components/PicksBoard";
import StaleBanner from "@/components/StaleBanner";
import OfflineShell from "@/components/OfflineShell";

export const revalidate = 300;

export default async function PlayPage() {
  const res = await getTopPicks(undefined, 200);
  if (!res.ok) return <OfflineShell />;

  const { picks, stale, date } = res.data;

  // Pool of pickable hitters (need a batter_id to grade).
  const pool: Pick[] = picks
    .filter((p) => p.batter_id !== null)
    .map((p) => ({
      batterId: p.batter_id as string,
      name: p.name,
      gameId: p.game_id,
      oppPitcher: p.opp_pitcher,
      pGameHr: p.p_game_hr,
      gameDatetime: p.game_datetime,
    }));

  return (
    <div>
      {stale && <StaleBanner date={date} />}
      <header className="mb-4">
        <h1 className="font-sans text-xl font-semibold text-text-pri">HR Pick&apos;em</h1>
        <p className="mt-0.5 font-mono text-xs text-text-muted">
          {formatDate(date) ?? date} · pick up to 5 to homer
        </p>
      </header>
      <PicksBoard pool={pool} today={date} />
    </div>
  );
}
