import Link from "next/link";
import { notFound } from "next/navigation";
import { getGame } from "@/lib/api";
import { isSideError } from "@/lib/types";
import { formatGameTime } from "@/lib/format";
import GameView from "@/components/GameView";
import PendingLineupHero from "@/components/PendingLineupHero";
import StaleBanner from "@/components/StaleBanner";
import OfflineShell from "@/components/OfflineShell";
import ParkBadge from "@/components/ParkBadge";

export const revalidate = 300;

export default async function GamePage({
  params,
}: {
  params: Promise<{ game_id: string }>;
}) {
  const { game_id } = await params;
  const res = await getGame(game_id);

  if (!res.ok) {
    if (res.status === 404) notFound();
    return <OfflineShell />;
  }

  const game = res.data;
  const time = formatGameTime(game.game_datetime);
  const bothErrored = isSideError(game.sides.away) && isSideError(game.sides.home);

  return (
    <div>
      {game.stale && <StaleBanner date={game.date} />}

      <Link href="/" className="mb-3 inline-block font-mono text-xs text-text-muted hover:text-text-pri">
        ‹ All games
      </Link>

      <header className="mb-5">
        <h1 className="font-sans text-xl font-semibold text-text-pri">
          {game.away_team} <span className="text-text-muted">@</span> {game.home_team}
        </h1>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs text-text-muted">
          {time && <span>{time}</span>}
          {game.venue && <span>{game.venue}</span>}
          <ParkBadge factor={game.park_factor} />
        </div>
        <div className="mt-1 font-mono text-xs text-text-muted">
          {game.away_sp} <span className="text-text-muted/60">vs</span> {game.home_sp}
        </div>
      </header>

      {bothErrored ? (
        <PendingLineupHero gameId={game.game_id} awaySp={game.away_sp} homeSp={game.home_sp} />
      ) : (
        <GameView game={game} />
      )}
    </div>
  );
}
