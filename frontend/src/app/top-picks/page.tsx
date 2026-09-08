import type { Metadata } from "next";
import { getOdds, getTopPicks } from "@/lib/api";
import { formatDate, formatPct } from "@/lib/format";
import type { OddsLine } from "@/lib/types";
import TopPickRow from "@/components/TopPickRow";
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

  const { picks, stale, date, excluded, gating } = res.data;

  // Hitters held back because their lineup is not fully posted or because the
  // opposing starter's stats were regressed to a league prior. Both make a
  // hitter's number look better than the evidence behind it, which on a ranked
  // board floats them straight to the top — so the board leaves them out and
  // says how many it left out rather than silently showing a shorter list.
  const heldBack = gating === "on" ? excluded?.total ?? 0 : 0;
  const heldBackParts: string[] = [];
  if (excluded?.lineup_not_posted) {
    heldBackParts.push(`${excluded.lineup_not_posted} awaiting lineups`);
  }
  if (excluded?.pitcher_regressed) {
    heldBackParts.push(`${excluded.pitcher_regressed} vs unproven starters`);
  }

  // Edge odds (when a feed exists). Joined by batter_id; absent until the
  // pipeline writes an odds file — the UI just omits edges, never fakes them.
  const oddsRes = await getOdds(date);
  const oddsBook = oddsRes.ok ? oddsRes.data.book : null;
  const oddsByBatter = new Map<string, OddsLine>();
  if (oddsRes.ok && oddsRes.data.available) {
    for (const line of oddsRes.data.odds) {
      if (line.batter_id) oddsByBatter.set(line.batter_id, line);
    }
  }
  const hasEdge = oddsByBatter.size > 0;

  return (
    <div>
      {stale && <StaleBanner date={date} />}

      <header className="mb-4">
        <h1 className="font-sans text-xl font-semibold text-text-pri">Today&apos;s Best Bets</h1>
        <p className="mt-0.5 font-mono text-xs text-text-muted">
          {formatDate(date) ?? date} · {picks.length} hitters predicted
          {hasEdge && oddsBook ? ` · edge vs ${oddsBook}` : ""}
        </p>
        {heldBack > 0 && (
          <p className="mt-1.5 font-mono text-xs text-text-muted">
            {heldBack} hitter{heldBack === 1 ? "" : "s"} held back
            {heldBackParts.length > 0 ? ` — ${heldBackParts.join(", ")}` : ""}.
            They rejoin the board once lineups post.
          </p>
        )}
      </header>

      {picks.length === 0 ? (
        <div className="rounded-lg border border-white/5 bg-card p-8 text-center font-sans text-sm text-text-muted">
          {heldBack > 0
            ? `No confirmed picks yet — ${heldBack} hitter${heldBack === 1 ? "" : "s"} waiting on posted lineups and probable starters.`
            : "No predictions yet for this slate."}
        </div>
      ) : (
        <div className="space-y-2">
          {picks.map((p) => (
            <TopPickRow
              key={`${p.rank}-${p.batter_id ?? p.name}`}
              pick={p}
              odds={p.batter_id ? oddsByBatter.get(p.batter_id) : undefined}
              oddsBook={oddsBook}
            />
          ))}
        </div>
      )}
    </div>
  );
}
