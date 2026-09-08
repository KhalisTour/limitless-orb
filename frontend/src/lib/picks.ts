/*
  Pick'em minigame state + scoring (PrizePicks/Underdog-style, but points-only,
  free-to-play). Anonymous, on-device: picks live in localStorage, graded
  against /api/results/{date} when finals are in.
*/

export const MAX_PICKS = 5;
export const POINTS_PER_CORRECT = 10;
const STORE_KEY = "orb_picks_v1";

export interface Pick {
  batterId: string;
  name: string;
  gameId: number;
  oppPitcher: string;
  pGameHr: number;
  gameDatetime: string | null;
}

/* A selectable hitter in the pool (richer than a stored Pick). */
export interface PoolHitter {
  batterId: string;
  name: string;
  gameId: number;
  oppPitcher: string;
  pGameHr: number;
  pPerPa: number;
  gameDatetime: string | null;
}

/* One game's collapsible column in the Pick'em nav. */
export interface GameGroup {
  gameId: number;
  label: string; // "MIN @ SF"
  timeET: string | null; // "6:45p ET"
  hitters: PoolHitter[]; // sorted Elite -> Low (pPerPa desc)
}

export function poolToPick(h: PoolHitter): Pick {
  return {
    batterId: h.batterId,
    name: h.name,
    gameId: h.gameId,
    oppPitcher: h.oppPitcher,
    pGameHr: h.pGameHr,
    gameDatetime: h.gameDatetime,
  };
}

export interface DayGrade {
  correct: number;
  total: number;
  points: number;
  perfect: boolean;
}

export interface DaySlate {
  date: string;
  picks: Pick[];
  lockedAt: string | null;
  graded: DayGrade | null;
}

export type PicksStore = Record<string, DaySlate>;

/* ---- persistence (client only) ---- */

export function loadStore(): PicksStore {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    return raw ? (JSON.parse(raw) as PicksStore) : {};
  } catch {
    return {};
  }
}

export function saveStore(store: PicksStore): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(store));
  } catch {
    /* quota / private mode — ignore */
  }
}

/* ---- scoring ---- */

/** Grade a day's picks against HR counts by batter_id. */
export function scoreDay(picks: Pick[], hrByBatter: Record<string, number>): DayGrade {
  const correct = picks.filter((p) => (hrByBatter[p.batterId] ?? 0) >= 1).length;
  const total = picks.length;
  const perfect = total >= 2 && correct === total;
  const base = correct * POINTS_PER_CORRECT;
  const points = perfect ? base * 2 : base;
  return { correct, total, points, perfect };
}

export interface PlayerStats {
  totalPoints: number;
  streak: number;
  daysPlayed: number;
  bestDay: number;
}

/** Aggregate lifetime stats + current streak (consecutive graded days, most
    recent backwards, with at least one correct pick). */
export function computeStats(store: PicksStore): PlayerStats {
  const graded = Object.values(store)
    .filter((d) => d.graded !== null)
    .sort((a, b) => (a.date < b.date ? 1 : -1)); // newest first

  let totalPoints = 0;
  let bestDay = 0;
  for (const d of graded) {
    totalPoints += d.graded!.points;
    if (d.graded!.points > bestDay) bestDay = d.graded!.points;
  }

  let streak = 0;
  for (const d of graded) {
    if (d.graded!.correct >= 1) streak += 1;
    else break;
  }

  return { totalPoints, streak, daysPlayed: graded.length, bestDay };
}

/* ---- lock helpers ---- */

/** Earliest first pitch among the picked games, as epoch ms (or null). */
export function earliestFirstPitch(picks: Pick[]): number | null {
  const times = picks
    .map((p) => (p.gameDatetime ? new Date(p.gameDatetime).getTime() : NaN))
    .filter((t) => Number.isFinite(t));
  return times.length ? Math.min(...times) : null;
}

/** First pitch for one game group, as epoch ms (or null when unknown). */
export function gameFirstPitch(g: GameGroup): number | null {
  const t = g.hitters[0]?.gameDatetime;
  if (!t) return null;
  const ms = new Date(t).getTime();
  return Number.isFinite(ms) ? ms : null;
}

/** A game is closed for picks once it has started. */
export function isGameLocked(g: GameGroup, now = Date.now()): boolean {
  const fp = gameFirstPitch(g);
  return fp !== null && now >= fp;
}

/**
 * Whether the whole slate is closed for editing.
 *
 * `games` matters. Without it the lock was derived only from the games the user
 * had already picked, so an empty slate was never locked — open Pick'em after
 * first pitch and the first pick was always allowed, and the moment it landed
 * the earliest first pitch jumped into the past, flipped this to true, and
 * unmounted the entire game list with no explanation. One pick, then the board
 * silently emptied. The slate is locked when the user locked it in, or when
 * every game on the board has already started.
 */
export function isSlateLocked(
  slate: DaySlate | undefined,
  now = Date.now(),
  games: GameGroup[] = [],
): boolean {
  if (slate?.lockedAt) return true;
  if (games.length > 0) return games.every((g) => isGameLocked(g, now));
  const fp = slate ? earliestFirstPitch(slate.picks) : null;
  return fp !== null && now >= fp;
}

/* ---- Poisson-binomial: distribution of how many of your picks homer ---- */

/** Returns array where index k = P(exactly k of the picks hit a HR). */
export function poissonBinomial(ps: number[]): number[] {
  let dist = [1];
  for (const p of ps) {
    const q = Math.max(0, Math.min(1, p));
    const next = new Array(dist.length + 1).fill(0);
    for (let k = 0; k < dist.length; k++) {
      next[k] += dist[k] * (1 - q);
      next[k + 1] += dist[k] * q;
    }
    dist = next;
  }
  return dist;
}

export function expectedHits(ps: number[]): number {
  return ps.reduce((a, b) => a + b, 0);
}
