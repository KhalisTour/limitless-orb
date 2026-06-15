/*
  Types mirror the VERIFIED API CONTRACT in PROMPT PART 1.
  Do not "improve" these shapes — they match live backend curl output.
  Nullable fields are nullable for a reason (rookies, pre-lineup games,
  older prediction JSONs).
*/

export interface HitterStats {
  barrel_pct: number | null;
  hardhit_pct: number | null;
  xslg: number | null;
  xba: number | null;
  xwoba: number | null;
  ev: number | null;
  la: number | null;
  whiff_pct: number | null;
  k_pct: number | null;
  bb_pct: number | null;
}

export interface HitterComponents {
  logistic: number;
  matchup: number;
  linear: number;
}

export interface Hitter {
  name: string;
  batter_id: string | null;
  lineup_slot: number;
  p_per_pa: number;
  p_per_pa_pctile: number;
  p_game_hr: number;
  p_multi_hr: number;
  exp_pa: number;
  tto_mult: number;
  park_factor: number | null;
  platoon_factor: number | null;
  components: HitterComponents;
  explanation: string | null;
  stats: HitterStats | null;
}

export interface Sim {
  n_games: number;
  team_hr_dist: Record<string, number>;
  p_at_least_one_hr: Record<string, number>;
  p_multi_hr: Record<string, number>;
  back_to_back_per_game: number;
}

export interface SideOk {
  pitcher: string; // OPPOSING pitcher (the one this side's hitters face)
  hitters: Hitter[];
  sim: Sim;
}

export interface SideError {
  error: string;
}

export type Side = SideOk | SideError;

export function isSideError(side: Side): side is SideError {
  return (side as SideError).error !== undefined;
}

export interface GameDetail {
  game_id: number;
  date: string;
  stale: boolean;
  away_team: string;
  home_team: string;
  away_sp: string;
  home_sp: string;
  game_datetime: string | null;
  venue: string | null;
  park_factor: number | null;
  sides: {
    away: Side;
    home: Side;
  };
}

export interface TopPickTeaser {
  name: string;
  side: "home" | "away";
  p_per_pa: number;
}

export interface GameListItem {
  game_id: number;
  away_team: string;
  home_team: string;
  away_sp: string;
  home_sp: string;
  game_datetime: string | null;
  venue: string | null;
  park_factor: number | null;
  top_pick: TopPickTeaser | null;
}

export interface GamesList {
  date: string;
  stale: boolean;
  games: GameListItem[];
}

/* Flat hitter object from /api/top-picks */
export interface TopPick extends Hitter {
  rank: number;
  game_id: number;
  away_team: string;
  home_team: string;
  game_datetime: string | null;
  opp_pitcher: string;
}

export interface TopPicksResponse {
  date: string;
  stale: boolean;
  picks: TopPick[];
}

export interface DatesResponse {
  dates: string[];
  latest: string;
  n: number;
}

export interface ZoneCell {
  hr_pct: number;
  avg_la: number;
  avg_ev: number;
  n: number;
}

export interface ZonesResponse {
  player_id: string;
  name: string;
  pitch_family: string;
  player_found: boolean;
  zones: Record<string, ZoneCell>;
  league_baseline: Record<string, ZoneCell>;
}

export interface MatchupZoneCell {
  danger_score: number;
  batter_hr_pct: number;
  pitcher_tendency: number;
  danger_pct: number;
}

export interface MatchupResponse {
  zones: Record<string, MatchupZoneCell>;
  hottest_zone: string | null;
  hottest_pitch_family: string | null;
  narrative: string;
}

export interface TrajectoryResponse {
  found: boolean;
  used_key: string;
  median_la: number;
  median_ev: number;
  apex_ft: number;
  distance_ft: number;
  trajectory: [number, number][];
}

export interface CalibrationBin {
  bin: string;
  predicted_avg: number;
  actual_rate: number;
  n: number;
}

export interface AccuracyCall {
  date: string;
  hitter: string;
  opp_pitcher: string;
  p_per_pa: number;
  actual_hr: number;
}

export interface AccuracyResponse {
  status: "ok" | "insufficient_data";
  n_predictions: number;
  n_hrs: number;
  rate_predicted: number;
  rate_actual: number;
  brier: number;
  auc: number;
  log_loss: number;
  calibration_bins: CalibrationBin[];
  best_calls: AccuracyCall[];
  worst_misses: AccuracyCall[];
}

/* Pick'em grading source. hr_by_batter_id maps batter_id -> HRs hit that day. */
export interface ResultsResponse {
  date: string;
  final: boolean;
  hr_by_batter_id: Record<string, number>;
}

export interface RefreshResponse {
  refreshed: boolean;
  refreshed_at?: string;
  rate_limited?: boolean;
  retry_in_s?: number;
  refresh_failed?: boolean;
  stderr?: string;
  game: GameDetail;
}

/* Discriminated union returned by every api.ts wrapper */
export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status?: number };

/* Probability label tiers (slate-relative, PART 2) */
export type ProbLabel = "ELITE" | "HIGH" | "MED" | "LOW";

/* Stat keys used by StatGrid + percentile context */
export const STAT_KEYS = [
  "barrel_pct",
  "hardhit_pct",
  "xslg",
  "ev",
  "la",
  "whiff_pct",
  "k_pct",
  "bb_pct",
] as const;
export type StatKey = (typeof STAT_KEYS)[number];
