import Link from "next/link";
import { getGame, getMatchup, getOdds, getTrajectory, getZones } from "@/lib/api";
import { isSideError, type Hitter } from "@/lib/types";
import MatchupHero from "@/components/MatchupHero";
import { EdgeDetail } from "@/components/EdgeBadge";
import MatchupZones from "@/components/MatchupZones";
import TrajectoryArc from "@/components/TrajectoryArc";
import { isTrajectoryVisible } from "@/lib/trajectory";
import StatGrid from "@/components/StatGrid";
import ModelViews from "@/components/ModelViews";
import PitchFamilyToggle from "@/components/PitchFamilyToggle";
import PitchBubbleChart from "@/components/PitchBubbleChart";
import StaleBanner from "@/components/StaleBanner";
import OfflineShell from "@/components/OfflineShell";

export const revalidate = 300;

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-6">
      <h2 className="mb-2 font-mono text-xs uppercase tracking-wide text-text-muted">{title}</h2>
      {children}
    </section>
  );
}

export default async function MatchupPage({
  params,
}: {
  params: Promise<{ game_id: string; batter_id: string; pitcher_name: string }>;
}) {
  const { game_id, batter_id, pitcher_name } = await params;
  // Next 16 does NOT auto-decode the route segment, so decode it ourselves
  // (error #16). Guard against a stray literal % that isn't an escape.
  let pitcher = pitcher_name;
  try {
    pitcher = decodeURIComponent(pitcher_name);
  } catch {
    pitcher = pitcher_name;
  }

  // game + zones + matchup in parallel; trajectory depends on hottest_zone.
  const [gameRes, zonesRes, matchupRes] = await Promise.all([
    getGame(game_id),
    getZones(batter_id, "all"),
    getMatchup(batter_id, pitcher, "all"),
  ]);

  if (!gameRes.ok) return <OfflineShell />;
  const game = gameRes.data;

  // locate the hitter across both sides
  let hitter: Hitter | undefined;
  for (const key of ["away", "home"] as const) {
    const side = game.sides[key];
    if (!isSideError(side)) {
      const found = side.hitters.find((h) => h.batter_id === batter_id);
      if (found) hitter = found;
    }
  }

  const matchup = matchupRes.ok
    ? matchupRes.data
    : { zones: {}, hottest_zone: null, hottest_pitch_family: null, narrative: "not_available" };
  const hasMatchup =
    matchup.narrative !== "not_available" && Object.keys(matchup.zones).length > 0;

  const requestedZone = matchup.hottest_zone ?? "5";
  const requestedFamily = "fastball";
  const trajRes = await getTrajectory(batter_id, requestedZone, requestedFamily);

  const zones = zonesRes.ok
    ? zonesRes.data
    : { player_found: false, zones: {}, league_baseline: {}, name: "", player_id: batter_id, pitch_family: "all" };

  const displayName = hitter?.name ?? zones.name ?? "Hitter";
  const pPerPa = hitter?.p_per_pa ?? 0;
  const pGameHr = hitter?.p_game_hr ?? 0;

  // Betting edge for this hitter (omitted until a real odds feed exists).
  const oddsRes = await getOdds(game.date);
  const oddsBook = oddsRes.ok ? oddsRes.data.book : null;
  const oddsLine =
    oddsRes.ok && oddsRes.data.available
      ? oddsRes.data.odds.find((o) => o.batter_id === batter_id)
      : undefined;

  return (
    <div>
      {game.stale && <StaleBanner date={game.date} />}

      <Link
        href={`/game/${game.game_id}`}
        className="mb-3 inline-block font-mono text-xs text-text-muted hover:text-text-pri"
      >
        ‹ {game.away_team} @ {game.home_team}
      </Link>

      {/* §1 HERO */}
      <MatchupHero
        batterId={batter_id}
        name={displayName}
        pitcher={pitcher}
        pPerPa={pPerPa}
        pGameHr={pGameHr}
      />

      {oddsLine && (
        <div className="mt-3">
          <EdgeDetail line={oddsLine} book={oddsBook} />
        </div>
      )}

      {/* §2 TARGETING RETICLE */}
      <Section title="Targeting">
        <MatchupZones
          matchupZones={matchup.zones}
          batterZones={zones.zones}
          baseline={zones.league_baseline}
          hottestZone={matchup.hottest_zone}
          hasMatchup={hasMatchup}
          playerFound={zones.player_found}
        />
        {hasMatchup && matchup.narrative !== "not_available" && (
          <p className="mt-3 font-sans text-sm text-text-pri">{matchup.narrative}</p>
        )}
        {hasMatchup && matchup.hottest_pitch_family && (
          <p className="mt-1 font-mono text-xs text-text-muted">
            Most dangerous: {matchup.hottest_pitch_family}
          </p>
        )}
        {!zones.player_found && (
          <p className="mt-2 font-mono text-xs text-text-muted">
            league avg — no player zone data
          </p>
        )}
        <div className="mt-3">
          <PitchFamilyToggle />
        </div>
      </Section>

      {/* §3 TRAJECTORY */}
      {trajRes.ok && isTrajectoryVisible(trajRes.data, requestedZone, requestedFamily) && (
        <Section title="Likeliest ball flight">
          <TrajectoryArc
            data={trajRes.data}
            requestedZone={requestedZone}
            requestedFamily={requestedFamily}
          />
        </Section>
      )}

      {/* §4 NUMBERS */}
      {hitter?.stats && (
        <Section title="The numbers">
          <StatGrid stats={hitter.stats} />
        </Section>
      )}

      {/* §5 PITCH-MIX MATCHUP */}
      {hitter?.tb?.families && hitter.tb.families.length > 0 && (
        <Section title="Pitch-mix matchup">
          <div className="rounded-xl border border-white/5 bg-card p-4">
            <p className="mb-3 font-mono text-xs text-text-muted">
              bubble size = how often thrown &nbsp;·&nbsp; number &amp; color = TB score (5 = league avg)
            </p>
            <PitchBubbleChart families={hitter.tb.families} />
          </div>
        </Section>
      )}

      {/* §6 MODEL VIEWS */}
      {hitter && (
        <Section title="Model views">
          <ModelViews components={hitter.components} ensemble={hitter.p_per_pa} />
        </Section>
      )}
    </div>
  );
}
