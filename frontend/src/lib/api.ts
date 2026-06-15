import type {
  AccuracyResponse,
  ApiResult,
  DatesResponse,
  GameDetail,
  GamesList,
  MatchupResponse,
  OddsResponse,
  RefreshResponse,
  ResultsResponse,
  TopPicksResponse,
  TrajectoryResponse,
  ZonesResponse,
} from "./types";
import * as mock from "./mock";

/*
  Typed fetch wrappers for every endpoint (PART 5). Each returns a
  discriminated ApiResult<T> — callers branch on `ok`, never try/catch.

  When NEXT_PUBLIC_API_URL is unset -> mock mode (entire frontend
  renderable before Railway is wired). When set -> live API.
*/

// Trailing slashes are a common copy-paste mistake in the Vercel env var;
// strip them so `${API_BASE}/api/...` never becomes `//api/...`.
export const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? "").replace(/\/+$/, "");
export const USE_MOCK = API_BASE === "";

const DEFAULT_REVALIDATE = 300;

interface FetchOpts {
  revalidate?: number;
}

async function liveFetch<T>(
  path: string,
  opts: FetchOpts = {},
): Promise<ApiResult<T>> {
  const url = `${API_BASE}${path}`;
  try {
    const res = await fetch(url, {
      // ISR: Vercel serves last good snapshot if Railway is down (error #1).
      next: { revalidate: opts.revalidate ?? DEFAULT_REVALIDATE },
    });
    if (!res.ok) {
      return { ok: false, error: `HTTP ${res.status}`, status: res.status };
    }
    const data = (await res.json()) as T;
    return { ok: true, data };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "network error" };
  }
}

function ok<T>(data: T): ApiResult<T> {
  return { ok: true, data };
}

/* ---- Endpoint wrappers ---- */

export async function getDates(): Promise<ApiResult<DatesResponse>> {
  if (USE_MOCK) return ok(mock.mockDates());
  return liveFetch<DatesResponse>(`/api/dates`);
}

export async function getGames(date?: string): Promise<ApiResult<GamesList>> {
  if (USE_MOCK) return ok(mock.mockGames(date));
  const q = date ? `?date=${encodeURIComponent(date)}` : "";
  return liveFetch<GamesList>(`/api/games${q}`);
}

export async function getGame(gameId: number | string): Promise<ApiResult<GameDetail>> {
  if (USE_MOCK) {
    const g = mock.mockGame(gameId);
    return g ? ok(g) : { ok: false, error: "not found", status: 404 };
  }
  return liveFetch<GameDetail>(`/api/game/${gameId}`);
}

export async function getTopPicks(
  date?: string,
  n = 50,
): Promise<ApiResult<TopPicksResponse>> {
  if (USE_MOCK) return ok(mock.mockTopPicks(date, n));
  const params = new URLSearchParams();
  if (date) params.set("date", date);
  params.set("n", String(n));
  return liveFetch<TopPicksResponse>(`/api/top-picks?${params.toString()}`);
}

export async function getZones(
  batterId: string,
  pitchFamily = "all",
): Promise<ApiResult<ZonesResponse>> {
  if (USE_MOCK) return ok(mock.mockZones(batterId, pitchFamily));
  return liveFetch<ZonesResponse>(
    `/api/zones/${batterId}?pitch_family=${encodeURIComponent(pitchFamily)}`,
  );
}

export async function getMatchup(
  batterId: string,
  pitcherName: string,
  pitchFamily = "all",
): Promise<ApiResult<MatchupResponse>> {
  if (USE_MOCK) return ok(mock.mockMatchup(batterId, pitcherName, pitchFamily));
  const params = new URLSearchParams();
  params.set("batter_id", batterId);
  params.set("pitcher_name", pitcherName); // URLSearchParams encodes for us
  params.set("pitch_family", pitchFamily);
  return liveFetch<MatchupResponse>(`/api/matchup?${params.toString()}`);
}

export async function getTrajectory(
  batterId: string,
  zone: string,
  pitchFamily = "fastball",
): Promise<ApiResult<TrajectoryResponse>> {
  if (USE_MOCK) return ok(mock.mockTrajectory(batterId, zone, pitchFamily));
  return liveFetch<TrajectoryResponse>(
    `/api/trajectory/${batterId}?zone=${encodeURIComponent(
      zone,
    )}&pitch_family=${encodeURIComponent(pitchFamily)}`,
  );
}

export async function getAccuracy(days = 30): Promise<ApiResult<AccuracyResponse>> {
  if (USE_MOCK) return ok(mock.mockAccuracy());
  return liveFetch<AccuracyResponse>(`/api/accuracy?days=${days}`, {
    revalidate: 3600,
  });
}

/* Pick'em grading source. Backend endpoint TBD (pipeline writes results_<date>);
   until it ships live calls 404 and the minigame shows "results pending". */
export async function getResults(date: string): Promise<ApiResult<ResultsResponse>> {
  if (USE_MOCK) return ok(mock.mockResults(date));
  return liveFetch<ResultsResponse>(`/api/results/${date}`, { revalidate: 3600 });
}

/* Betting edge. We deliberately do NOT mock odds — without a real feed the
   edge UI stays empty rather than showing fabricated lines. Live calls
   /api/odds/{date}, which returns available:false until the pipeline's odds
   fetch has written a file. */
export async function getOdds(date: string): Promise<ApiResult<OddsResponse>> {
  if (USE_MOCK) {
    return ok({ date, available: false, book: null, pulled_at: null, odds: [] });
  }
  return liveFetch<OddsResponse>(`/api/odds/${date}`, { revalidate: 300 });
}

/* Client-side POST — refresh a game's lineup/prediction (error #17). */
export async function refreshGame(
  gameId: number | string,
): Promise<ApiResult<RefreshResponse>> {
  if (USE_MOCK) return ok(mock.mockRefresh(gameId));
  try {
    const res = await fetch(`${API_BASE}/api/refresh/${gameId}`, {
      method: "POST",
    });
    // /api/refresh always returns 200; flags live in the body.
    const data = (await res.json()) as RefreshResponse;
    return { ok: true, data };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "network error" };
  }
}
