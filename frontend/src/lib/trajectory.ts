import type { TrajectoryResponse } from "./types";

/* Pure trajectory helpers — shared by the server page and the client arc
   component, so they live outside the "use client" boundary. */

export function parseTrajKey(usedKey: string): { zone: string | null; family: string | null } {
  // format: "zone_{z}_{family}"
  const m = usedKey.match(/^zone_([^_]+)_(.+)$/);
  if (!m) return { zone: null, family: null };
  return { zone: m[1], family: m[2] };
}

/** Whether to render the trajectory section at all (error #10/#11):
    found:false or both-axis fallback -> hidden. */
export function isTrajectoryVisible(
  data: TrajectoryResponse,
  requestedZone: string,
  requestedFamily: string,
): boolean {
  if (!data.found) return false;
  if (data.trajectory.length === 0 || data.distance_ft <= 0 || data.apex_ft <= 0) return false;
  const parsed = parseTrajKey(data.used_key);
  const zoneDiff = parsed.zone !== null && parsed.zone !== requestedZone;
  const familyDiff = parsed.family !== null && parsed.family !== requestedFamily;
  return !(zoneDiff && familyDiff);
}

/** Single-axis fallback -> show arc with a dim caption. */
export function isSingleAxisFallback(
  data: TrajectoryResponse,
  requestedZone: string,
  requestedFamily: string,
): boolean {
  const parsed = parseTrajKey(data.used_key);
  const zoneDiff = parsed.zone !== null && parsed.zone !== requestedZone;
  const familyDiff = parsed.family !== null && parsed.family !== requestedFamily;
  return (zoneDiff || familyDiff) && !(zoneDiff && familyDiff);
}
