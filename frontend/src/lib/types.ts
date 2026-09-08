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

/* Per-pitch-family detail for the matchup overlay (tensor etb vs pitcher usage). */
export interface FamilyMatchup {
  family: string;
  etb: number;
  usage: number;
}

/* XBH prediction: calibrated logistic level + tensor matchup delta.
   Nullable across the board for older prediction JSONs that predate the tensor. */
export interface XbhPrediction {
  per_pa_level: number;
  tensor_delta: number;
  per_pa: number;
  exp_per_game: number;
}

/* Total-bases prediction: same shape + the per-family breakdown for the overlay. */
export interface TbPrediction extends XbhPrediction {
  families: FamilyMatchup[] | null;
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
  tb: TbPrediction | null;
  xbh: XbhPrediction | null;
  /** Per-PA hit probability from models_hit.py. Null on predictions generated
   *  before the hit model was scored separately from the HR model. */
  hit: XbhPrediction | null;
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
  /** Whether this side is fit to publish picks from. Absent on older payloads. */
  quality?: SideQuality;
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
  /** Built on a lineup that is not fully posted, or against a starter whose
   *  stats were regressed to a prior. Shown on game cards, gated off the
   *  top-picks board. */
  provisional?: boolean;
}

/** Why a game-side is or is not fit to publish a pick from. */
export type ProvisionalReason = "lineup_not_posted" | "pitcher_regressed";

export interface SideQuality {
  /** False for predictions generated before data-quality tracking existed. */
  known: boolean;
  publishable: boolean;
  reasons: ProvisionalReason[];
  lineup_confirmed?: boolean | null;
  lineup_slots?: number | null;
  lineup_hitters?: number | null;
  pitcher_level?: string | null;
  pitcher_regressed?: boolean | null;
  pitcher_n_pa?: number | null;
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
  provisional?: boolean;
  provisional_reasons?: ProvisionalReason[];
}

export interface TopPicksResponse {
  date: string;
  stale: boolean;
  picks: TopPick[];
  /** How many hitters the data-quality gate held back, and why. */
  excluded?: {
    total: number;
    lineup_not_posted: number;
    pitcher_regressed: number;
  };
  /** "on"  — the gate ran and filtered this slate.
   *  "off" — caller asked for provisional picks too.
   *  "none"— this date predates data-quality tracking; nothing was filtered. */
  gating?: "on" | "off" | "none";
}

/* /api/slate-context — label thresholds + stat percentile curves, computed
   server-side over the whole (ungated) slate. */
export interface SlateContext {
  date: string;
  stale: boolean;
  n_hitters: number;
  thresholds: { elite: number; high: number; med: number };
  /** 101-point percentile curve per stat; index i is the i-th percentile. */
  stat_percentiles: Record<string, number[]>;
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
  /** Brier a constant league-rate forecast would score over the same window.
   *  A model that does not beat it is not adding information. */
  baseline_brier?: number;
  beats_baseline?: boolean;
  auc: number;
  log_loss: number;
  calibration_bins: CalibrationBin[];
  best_calls: AccuracyCall[];
  worst_misses: AccuracyCall[];
}

/* Betting edge — from /api/odds/{date}. */
export interface OddsLine {
  batter_id: string | null;
  name: string;
  american: number | null;
  implied: number | null;
  model_p: number | null;
  edge: number | null;
  ev: number | null;
}

export interface OddsResponse {
  date: string;
  available: boolean;
  book: string | null;
  pulled_at: string | null;
  odds: OddsLine[];
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
