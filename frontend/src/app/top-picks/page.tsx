import type { Metadata } from "next";
import { getTopPicks } from "@/lib/api";
import { formatDate, formatPct } from "@/lib/format";
import HitterCard from "@/components/HitterCard";
import StaleBanner from "@/components/StaleBanner";
import OfflineShell from "@/components/OfflineShell";

export const revalidate = 300;

export async function generateMetadata(): Promise<Metadata> {
  const res = await getTopPicks(undefined, 3);
  if (!res.ok || res.data.picks.length === 0) {
    return { title: "Today's Best Bets — Orb AI" };
  }
  const [p1, p2, p3] = res.data.picks;
  const title = `Tonight's top HR pick: ${p1.name} (${formatPct(p1.p_per_pa)})`;
  const others = [p2, p3].filter(Boolean).map((p) => `${p.name} ${formatPct(p.p_per_pa)}`);
  const description = others.length
    ? `Also watching: ${others.join(", ")}.`
    : "Daily home-run probability predictions.";
  return {
    title,
    description,
    openGraph: { title, description },
    twitter: { card: "summary", title, description },
  };
}

export default async function TopPicksPage() {
  const res = await getTopPicks(undefined, 50);
  if (!res.ok) return <OfflineShell />;

  const { picks, stale, date } = res.data;

  return (
    <div>
      {stale && <StaleBanner date={date} />}

      <header className="mb-4">
        <h1 className="font-sans text-xl font-semibold text-text-pri">Today&apos;s Best Bets</h1>
        <p className="mt-0.5 font-mono text-xs text-text-muted">
          {formatDate(date) ?? date} · {picks.length} hitters predicted
        </p>
      </header>

      {picks.length === 0 ? (
        <div className="rounded-lg border border-white/5 bg-card p-8 text-center font-sans text-sm text-text-muted">
          No predictions yet for this slate.
        </div>
      ) : (
        <div className="space-y-2">
          {picks.map((p) => (
            <HitterCard
              key={`${p.rank}-${p.batter_id ?? p.name}`}
              hitter={p}
              variant="compact"
              gameId={p.game_id}
              oppPitcher={p.opp_pitcher}
              gameDatetime={p.game_datetime}
              rank={p.rank}
            />
          ))}
        </div>
      )}
    </div>
  );
}
