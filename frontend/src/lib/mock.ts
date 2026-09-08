import type {
  AccuracyResponse,
  DatesResponse,
  GameDetail,
  GameListItem,
  GamesList,
  Hitter,
  MatchupResponse,
  RefreshResponse,
  ResultsResponse,
  Sim,
  SlateContext,
  TopPick,
  TopPicksResponse,
  TrajectoryResponse,
  ZoneCell,
  ZonesResponse,
} from "./types";
import { STAT_KEYS } from "./types";

/*
  Realistic mock data covering EVERY contract edge case (PART 5):
  - 2 games full sides, 1 one-side-errored, 1 both-sides-errored
  - rookie (batter_id null), stats-null hitter, partial-stats hitter
  - game_datetime null (older prediction), stale:true response

  Used whenever NEXT_PUBLIC_API_URL is unset so the whole UI renders
  before Railway is wired.
*/

const MOCK_DATE = "2026-06-14";

const ZONE_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "11", "12", "13", "14"];

function makeStats(seed: number): Hitter["stats"] {
  return {
    barrel_pct: 4 + (seed % 13),
    hardhit_pct: 28 + (seed % 22),
    xslg: 0.32 + (seed % 25) / 100,
    xba: 0.21 + (seed % 9) / 100,
    xwoba: 0.28 + (seed % 12) / 100,
    ev: 86 + (seed % 9),
    la: 6 + (seed % 18),
    whiff_pct: 18 + (seed % 14),
    k_pct: 16 + (seed % 12),
    bb_pct: 5 + (seed % 8),
  };
}

let hitterSeed = 1;
function makeHitter(
  name: string,
  slot: number,
  pPerPa: number,
  opts: Partial<Hitter> = {},
): Hitter {
  const seed = hitterSeed++;
  const batterId = `6450${(seed + 10).toString().padStart(2, "0")}`;
  const expPa = 4.4;
  // TB/XBH mock: calibrated-ish levels + a tensor delta that swings sign by seed
  // so the EDGE/FADE chip and the per-family chart both demo across mock cards.
  const xbhLevel = Math.min(0.3, pPerPa * 2.5);
  const hitLevel = Math.min(0.45, 0.21 + pPerPa * 3.0);
  const tbLevel = hitLevel + xbhLevel + 2 * pPerPa;
  const tbDelta = ((seed % 7) - 3) / 250; // ~ -0.012 .. +0.016
  const xbhDelta = tbDelta * 0.6;
  const fams = ["four_seam", "sinker", "cutter", "slider", "change", "curve", "split", "kn"];
  const usageRaw = [33, 9, 7, 22, 11, 10, 7, 1].map((u, i) => u + ((seed + i) % 5));
  const usageSum = usageRaw.reduce((a, b) => a + b, 0);
  const families = fams.map((f, i) => ({
    family: f,
    etb: Math.round((0.32 + ((seed * 7 + i * 13) % 17) / 100) * 1000) / 1000,
    usage: Math.round((usageRaw[i] / usageSum) * 1000) / 1000,
  }));
  return {
    name,
    batter_id: batterId,
    lineup_slot: slot,
    p_per_pa: pPerPa,
    p_per_pa_pctile: Math.round(pPerPa / 0.001),
    p_game_hr: Math.min(0.1, pPerPa * 4.4),
    p_multi_hr: pPerPa * 0.04,
    exp_pa: expPa,
    tto_mult: 1.1 + (seed % 5) / 50,
    park_factor: 1.0 + ((seed % 7) - 3) / 20,
    platoon_factor: 0.95 + (seed % 5) / 50,
    components: {
      logistic: pPerPa * 0.85,
      matchup: pPerPa * 1.25,
      linear: pPerPa * 0.55,
    },
    explanation: null,
    stats: makeStats(seed),
    xbh: {
      per_pa_level: xbhLevel,
      tensor_delta: xbhDelta,
      per_pa: Math.max(0, xbhLevel + xbhDelta),
      exp_per_game: Math.max(0, xbhLevel + xbhDelta) * expPa,
    },
    tb: {
      per_pa_level: tbLevel,
      tensor_delta: tbDelta,
      per_pa: Math.max(0, tbLevel + tbDelta),
      exp_per_game: Math.max(0, tbLevel + tbDelta) * expPa,
      families,
    },
    hit: {
      per_pa_level: hitLevel,
      tensor_delta: 0,
      per_pa: hitLevel,
      exp_per_game: hitLevel * expPa,
    },
    ...opts,
  };
}

function makeSim(names: string[]): Sim {
  const p_at_least_one_hr: Record<string, number> = {};
  const p_multi_hr: Record<string, number> = {};
  names.forEach((n, i) => {
    p_at_least_one_hr[n] = Math.min(0.1, 0.03 + i * 0.012);
    p_multi_hr[n] = 0.001 + i * 0.0004;
  });
  return {
    n_games: 1000,
    team_hr_dist: {
      "2": 0.23,
      "0": 0.258,
      "1": 0.384,
      "4": 0.04,
      "3": 0.078,
      "5": 0.008,
      "6": 0.002,
    },
    p_at_least_one_hr,
    p_multi_hr,
    back_to_back_per_game: 0.041,
  };
}

/* ---- Games ---- */

interface MockGame extends GameDetail {}

function buildMinimalGames(): MockGame[] {
  // Screenshot/demo slate: exactly 1 game, 3 batters, with values that light
  // up each emblem (hitter-friendly park, a platoon edge, a barrel leader).
  hitterSeed = 1;
  const hitters = [
    makeHitter("Cal Raleigh", 3, 0.0775, {
      park_factor: 1.23,
      platoon_factor: 1.12,
      stats: { ...makeStats(2)!, barrel_pct: 17.4, hardhit_pct: 51.2 },
    }),
    makeHitter("Julio Rodríguez", 1, 0.0625, {
      park_factor: 1.23,
      platoon_factor: 0.98,
      stats: { ...makeStats(5)!, barrel_pct: 11.2 },
    }),
    makeHitter("Eugenio Suárez", 4, 0.0512, {
      park_factor: 1.23,
      platoon_factor: 1.07,
      stats: { ...makeStats(8)!, barrel_pct: 8.1 },
    }),
  ];
  const g: MockGame = {
    game_id: 824830,
    date: MOCK_DATE,
    stale: false,
    away_team: "Seattle Mariners",
    home_team: "Baltimore Orioles",
    away_sp: "Logan Gilbert",
    home_sp: "Trevor Rogers",
    game_datetime: "2026-06-14T23:05:00Z",
    venue: "Oriole Park at Camden Yards",
    park_factor: 1.23,
    sides: {
      away: { pitcher: "Trevor Rogers", hitters, sim: makeSim(hitters.map((h) => h.name)) },
      home: { error: "No home lineup yet for game 824830" },
    },
  };
  return [g];
}

function buildGames(): MockGame[] {
  if (process.env.NEXT_PUBLIC_MOCK_MINIMAL === "1") return buildMinimalGames();
  hitterSeed = 1;

  // Game 1 — full both sides (Mariners @ Orioles)
  const g1Away = [
    makeHitter("Julio Rodríguez", 1, 0.0625),
    makeHitter("Cal Raleigh", 3, 0.0775),
    makeHitter("Eugenio Suárez", 4, 0.0512),
    makeHitter("Mitch Garver", 6, 0.0331),
  ];
  const g1Home = [
    makeHitter("Gunnar Henderson", 1, 0.0588),
    makeHitter("Adley Rutschman", 2, 0.0426),
    makeHitter("Anthony Santander", 4, 0.0701),
    makeHitter("Ryan Mountcastle", 5, 0.0298),
  ];

  // Game 2 — full both sides (Yankees @ Red Sox) with special hitters
  const g2Away = [
    makeHitter("Aaron Judge", 2, 0.0772),
    // rookie: batter_id null -> disabled matchup CTA
    makeHitter("Jasson Domínguez", 7, 0.0388, { batter_id: null }),
    // stats null entirely -> stat grid hidden
    makeHitter("Anthony Volpe", 9, 0.0214, { stats: null }),
    makeHitter("Juan Soto", 3, 0.0689),
  ];
  const g2Home = [
    makeHitter("Rafael Devers", 3, 0.0644),
    // partial stats: some fields null
    makeHitter("Triston Casas", 5, 0.0455, {
      stats: {
        barrel_pct: 9.1,
        hardhit_pct: 44.2,
        xslg: 0.498,
        xba: null,
        xwoba: 0.351,
        ev: 91.3,
        la: null,
        whiff_pct: 27.5,
        k_pct: null,
        bb_pct: 11.2,
      },
    }),
    makeHitter("Jarren Duran", 1, 0.0402),
    makeHitter("Wilyer Abreu", 6, 0.0277),
  ];

  const g1: MockGame = {
    game_id: 824830,
    date: MOCK_DATE,
    stale: false,
    away_team: "Seattle Mariners",
    home_team: "Baltimore Orioles",
    away_sp: "Logan Gilbert",
    home_sp: "Trevor Rogers",
    game_datetime: "2026-06-14T23:05:00Z",
    venue: "Oriole Park at Camden Yards",
    park_factor: 1.2304,
    sides: {
      // sides.away.pitcher = opposing pitcher = home_sp
      away: { pitcher: "Trevor Rogers", hitters: g1Away, sim: makeSim(g1Away.map((h) => h.name)) },
      home: { pitcher: "Logan Gilbert", hitters: g1Home, sim: makeSim(g1Home.map((h) => h.name)) },
    },
  };

  const g2: MockGame = {
    game_id: 824831,
    date: MOCK_DATE,
    stale: false,
    away_team: "New York Yankees",
    home_team: "Boston Red Sox",
    away_sp: "Carlos Rodón",
    home_sp: "Brayan Bello",
    game_datetime: "2026-06-14T23:10:00Z",
    venue: "Fenway Park",
    park_factor: 1.0412,
    sides: {
      away: { pitcher: "Brayan Bello", hitters: g2Away, sim: makeSim(g2Away.map((h) => h.name)) },
      home: { pitcher: "Carlos Rodón", hitters: g2Home, sim: makeSim(g2Home.map((h) => h.name)) },
    },
  };

  // Game 3 — away errored, home ok, game_datetime null (older prediction)
  const g3Home = [
    makeHitter("Mookie Betts", 1, 0.0533),
    makeHitter("Shohei Ohtani", 2, 0.0768),
    makeHitter("Freddie Freeman", 3, 0.0491),
    makeHitter("Will Smith", 5, 0.0356),
  ];
  const g3: MockGame = {
    game_id: 824832,
    date: MOCK_DATE,
    stale: false,
    away_team: "San Francisco Giants",
    home_team: "Los Angeles Dodgers",
    away_sp: "Logan Webb",
    home_sp: "Tyler Glasnow",
    game_datetime: null,
    venue: "Dodger Stadium",
    park_factor: 0.9231,
    sides: {
      away: { error: "No away lineup yet for game 824832" },
      home: { pitcher: "Logan Webb", hitters: g3Home, sim: makeSim(g3Home.map((h) => h.name)) },
    },
  };

  // Game 4 — both sides errored
  const g4: MockGame = {
    game_id: 824833,
    date: MOCK_DATE,
    stale: false,
    away_team: "Chicago Cubs",
    home_team: "St. Louis Cardinals",
    away_sp: "Justin Steele",
    home_sp: "Sonny Gray",
    game_datetime: "2026-06-15T00:15:00Z",
    venue: "Busch Stadium",
    park_factor: 0.9912,
    sides: {
      away: { error: "No away lineup yet for game 824833" },
      home: { error: "No home lineup yet for game 824833" },
    },
  };

  return [g1, g2, g3, g4];
}

const GAMES = buildGames();

function topPickTeaser(g: GameDetail): GameListItem["top_pick"] {
  const cands: { name: string; side: "home" | "away"; p: number }[] = [];
  (["away", "home"] as const).forEach((sideKey) => {
    const side = g.sides[sideKey];
    if ("hitters" in side) {
      side.hitters.forEach((h) => cands.push({ name: h.name, side: sideKey, p: h.p_per_pa }));
    }
  });
  if (cands.length === 0) return null;
  const best = cands.reduce((a, b) => (b.p > a.p ? b : a));
  return { name: best.name, side: best.side, p_per_pa: best.p };
}

export function mockDates(): DatesResponse {
  return {
    dates: ["2026-06-14", "2026-06-13", "2026-06-12", "2026-06-11"],
    latest: "2026-06-14",
    n: 4,
  };
}

export function mockGames(date?: string): GamesList {
  const stale = date !== undefined && date !== MOCK_DATE;
  return {
    date: MOCK_DATE,
    stale,
    games: GAMES.map((g) => ({
      game_id: g.game_id,
      away_team: g.away_team,
      home_team: g.home_team,
      away_sp: g.away_sp,
      home_sp: g.home_sp,
      game_datetime: g.game_datetime,
      venue: g.venue,
      park_factor: g.park_factor,
      top_pick: topPickTeaser(g),
    })),
  };
}

export function mockGame(gameId: number | string): GameDetail | null {
  const id = Number(gameId);
  const g = GAMES.find((x) => x.game_id === id);
  return g ?? null;
}

export function mockTopPicks(date?: string, n = 50): TopPicksResponse {
  const stale = date !== undefined && date !== MOCK_DATE;
  const picks: TopPick[] = [];
  GAMES.forEach((g) => {
    (["away", "home"] as const).forEach((sideKey) => {
      const side = g.sides[sideKey];
      if ("hitters" in side) {
        side.hitters.forEach((h) => {
          picks.push({
            ...h,
            rank: 0,
            game_id: g.game_id,
            away_team: g.away_team,
            home_team: g.home_team,
            game_datetime: g.game_datetime,
            opp_pitcher: side.pitcher,
          });
        });
      }
    });
  });
  picks.sort((a, b) => b.p_per_pa - a.p_per_pa);
  picks.forEach((p, i) => (p.rank = i + 1));
  // Mock a gated slate so the "held back" line renders in mock mode too.
  return {
    date: MOCK_DATE,
    stale,
    picks: picks.slice(0, n),
    excluded: { total: 9, lineup_not_posted: 9, pitcher_regressed: 0 },
    gating: "on",
  };
}

/* Slate context for mock mode. Derived from the same fixture hitters the rest
   of mock.ts serves, so labels and percentile dots stay self-consistent — it
   does not introduce numbers of its own. */
export function mockSlateContext(date?: string): SlateContext {
  const hitters: Hitter[] = [];
  GAMES.forEach((g) =>
    (["away", "home"] as const).forEach((k) => {
      const side = g.sides[k];
      if ("hitters" in side) hitters.push(...side.hitters);
    }),
  );
  const q = (arr: number[], p: number): number => {
    if (arr.length === 0) return 0;
    const pos = (arr.length - 1) * p;
    const b = Math.floor(pos);
    const rest = pos - b;
    return arr[b + 1] !== undefined ? arr[b] + rest * (arr[b + 1] - arr[b]) : arr[b];
  };
  const ps = hitters.map((h) => h.p_per_pa).sort((a, b) => a - b);
  const stat_percentiles: Record<string, number[]> = {};
  for (const key of STAT_KEYS) {
    const vals = hitters
      .map((h) => h.stats?.[key])
      .filter((v): v is number => typeof v === "number" && Number.isFinite(v))
      .sort((a, b) => a - b);
    stat_percentiles[key] = Array.from({ length: 101 }, (_, i) => q(vals, i / 100));
  }
  return {
    date: MOCK_DATE,
    stale: date !== undefined && date !== MOCK_DATE,
    n_hitters: hitters.length,
    thresholds: { elite: q(ps, 0.9), high: q(ps, 0.75), med: q(ps, 0.5) },
    stat_percentiles,
  };
}

function makeZoneCell(seed: number, base: number): ZoneCell {
  return {
    hr_pct: Math.max(0, base + (Math.sin(seed) * 0.025)),
    avg_la: 12 + (seed % 20),
    avg_ev: 88 + (seed % 10),
    n: 12 + ((seed * 7) % 90),
  };
}

export function mockZones(batterId: string, pitchFamily = "all"): ZonesResponse {
  // Special-case one id to exercise player_found:false path.
  const found = batterId !== "645099";
  const zones: Record<string, ZoneCell> = {};
  const league_baseline: Record<string, ZoneCell> = {};
  ZONE_KEYS.forEach((k, i) => {
    const hot = k === "5" || k === "6";
    if (found) zones[k] = makeZoneCell(i + 3, hot ? 0.06 : 0.03);
    league_baseline[k] = makeZoneCell(i + 1, 0.032);
  });
  return {
    player_id: batterId,
    name: "Mock, Hitter",
    pitch_family: pitchFamily,
    player_found: found,
    zones: found ? zones : {},
    league_baseline,
  };
}

export function mockMatchup(
  batterId: string,
  pitcherName: string,
  _pitchFamily = "all",
): MatchupResponse {
  // Special-case to exercise not_available path.
  if (batterId === "645099" || pitcherName === "Unknown Pitcher") {
    return { zones: {}, hottest_zone: null, hottest_pitch_family: null, narrative: "not_available" };
  }
  const zones: Record<string, MatchupResponse["zones"][string]> = {};
  ZONE_KEYS.forEach((k, i) => {
    const hot = k === "5";
    const danger = hot ? 0.085 : Math.max(0.01, 0.03 + Math.sin(i) * 0.02);
    zones[k] = {
      danger_score: danger,
      batter_hr_pct: hot ? 0.062 : 0.028 + (i % 4) * 0.005,
      pitcher_tendency: 0.1 + (i % 5) * 0.04,
      danger_pct: danger,
    };
  });
  return {
    zones,
    hottest_zone: "5",
    hottest_pitch_family: "fastball",
    narrative: `${pitcherName} lives middle-in with the fastball, and this hitter does the most damage in zone 5. Watch for a mistake over the heart of the plate.`,
  };
}

export function mockTrajectory(
  batterId: string,
  zone: string,
  pitchFamily = "fastball",
): TrajectoryResponse {
  // batterId 645099 -> not found (hide panel)
  if (batterId === "645099") {
    return {
      found: false,
      used_key: "",
      median_la: 0,
      median_ev: 0,
      apex_ft: 0,
      distance_ft: 0,
      trajectory: [],
    };
  }
  const distance = 412;
  const apex = 98;
  const pts: [number, number][] = [];
  const n = 24;
  for (let i = 0; i <= n; i++) {
    const x = (distance * i) / n;
    const t = i / n;
    const y = 3 + 4 * apex * t * (1 - t); // parabola from ~3ft to apex back to 0
    pts.push([Math.round(x), Math.max(0, Math.round(y * 10) / 10)]);
  }
  return {
    found: true,
    used_key: `zone_${zone}_${pitchFamily}`,
    median_la: 28.5,
    median_ev: 104.2,
    apex_ft: apex,
    distance_ft: distance,
    trajectory: pts,
  };
}

export function mockAccuracy(): AccuracyResponse {
  return {
    status: "ok",
    n_predictions: 360,
    n_hrs: 53,
    rate_predicted: 0.033,
    rate_actual: 0.147,
    brier: 0.137,
    auc: 0.647,
    log_loss: 0.401,
    calibration_bins: [
      { bin: "0-2%", predicted_avg: 0.015, actual_rate: 0.012, n: 40 },
      { bin: "2-4%", predicted_avg: 0.03, actual_rate: 0.028, n: 120 },
      { bin: "4-6%", predicted_avg: 0.049, actual_rate: 0.052, n: 95 },
      { bin: "6-8%", predicted_avg: 0.068, actual_rate: 0.061, n: 70 },
      { bin: "8%+", predicted_avg: 0.091, actual_rate: 0.103, n: 35 },
    ],
    best_calls: [
      { date: "2026-06-12", hitter: "Aaron Judge", opp_pitcher: "Brayan Bello", p_per_pa: 0.078, actual_hr: 1 },
      { date: "2026-06-11", hitter: "Shohei Ohtani", opp_pitcher: "Logan Webb", p_per_pa: 0.071, actual_hr: 1 },
      { date: "2026-06-10", hitter: "Cal Raleigh", opp_pitcher: "Trevor Rogers", p_per_pa: 0.069, actual_hr: 1 },
    ],
    worst_misses: [
      { date: "2026-06-12", hitter: "Anthony Volpe", opp_pitcher: "Bello", p_per_pa: 0.021, actual_hr: 1 },
      { date: "2026-06-11", hitter: "Wilyer Abreu", opp_pitcher: "Rodón", p_per_pa: 0.027, actual_hr: 1 },
    ],
  };
}

export function mockResults(date: string): ResultsResponse {
  // Today (and future) = not final yet. Past dates = a few batters homered,
  // so the minigame's grading/streak loop is demoable offline.
  const isPastOrToday = date <= MOCK_DATE;
  const final = date < MOCK_DATE;
  const hr_by_batter_id: Record<string, number> = {};
  if (final && isPastOrToday) {
    // deterministic-ish: every 3rd mock batter_id "homered"
    for (let seed = 1; seed <= 40; seed++) {
      const id = `6450${(seed + 10).toString().padStart(2, "0")}`;
      if (seed % 3 === 0) hr_by_batter_id[id] = 1;
      if (seed % 7 === 0) hr_by_batter_id[id] = 2;
    }
  }
  return { date, final, hr_by_batter_id };
}

export function mockRefresh(gameId: number | string): RefreshResponse {
  const g = mockGame(gameId) ?? GAMES[0];
  return {
    refreshed: true,
    refreshed_at: new Date().toISOString(),
    game: g,
  };
}
