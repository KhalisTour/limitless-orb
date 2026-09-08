import { getDates, getGames } from "@/lib/api";
import GameCard from "@/components/GameCard";
import DatePicker from "@/components/DatePicker";
import StaleBanner from "@/components/StaleBanner";
import OfflineShell from "@/components/OfflineShell";

/* Page 1 — Games list. ISR 5 min (PART 6 / PART 7). */
export const revalidate = 300;

export default async function HomePage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string }>;
}) {
  const { date } = await searchParams;
  const [gamesRes, datesRes] = await Promise.all([getGames(date), getDates()]);

  if (!gamesRes.ok) {
    return <OfflineShell />;
  }

  const games = gamesRes.data;
  const dates = datesRes.ok ? datesRes.data.dates : [];
  const currentDate = games.date;

  return (
    <div>
      {games.stale && <StaleBanner date={games.date} />}

      <div className="mb-4 flex items-center justify-between">
        <h1 className="font-sans text-lg font-semibold text-text-pri">Today&apos;s Slate</h1>
        {dates.length > 0 && <DatePicker dates={dates} current={currentDate} />}
      </div>

      {games.games.length === 0 ? (
        <div className="rounded-lg border border-white/5 bg-card p-8 text-center font-sans text-sm text-text-muted">
          No games on the board for this date.
        </div>
      ) : (
        <div className="space-y-2.5">
          {games.games.map((g) => (
            <GameCard key={g.game_id} game={g} date={currentDate} />
          ))}
        </div>
      )}
    </div>
  );
}
