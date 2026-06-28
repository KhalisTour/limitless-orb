/*
  The single shared color utility (PART 2 / PART 3).
  Import zoneColor() everywhere — ZoneGrid (all modes), MonteCarloBar
  buckets, ProbabilityRing stroke. Never duplicate this scale.

  Cold -> hot, 6 stops by hr_pct. Teal (#26a69a) at 0.032 is the PER-PA
  league-average visual zero point. Linear interpolation between stops.
*/

const SCALE: [number, [number, number, number]][] = [
  [0.0, [0x1a, 0x23, 0x7e]], // deep blue
  [0.015, [0x02, 0x88, 0xd1]],
  [0.032, [0x26, 0xa6, 0x9a]], // teal — league average per-PA
  [0.05, [0xfd, 0xd8, 0x35]], // yellow
  [0.07, [0xff, 0x57, 0x22]], // orange-red
  [0.1, [0xd5, 0x00, 0x00]], // deep red
];

function lerp(a: number, b: number, t: number): number {
  return Math.round(a + (b - a) * t);
}

function toHex(c: number): string {
  return c.toString(16).padStart(2, "0");
}

/** Map an hr_pct (or any probability on the 0–0.10 scale) to a hex color. */
export function zoneColor(hrPct: number): string {
  if (!Number.isFinite(hrPct)) return "#26a69a";
  const v = Math.max(0, Math.min(0.1, hrPct));

  for (let i = 0; i < SCALE.length - 1; i++) {
    const [lo, loColor] = SCALE[i];
    const [hi, hiColor] = SCALE[i + 1];
    if (v >= lo && v <= hi) {
      const t = hi === lo ? 0 : (v - lo) / (hi - lo);
      const r = lerp(loColor[0], hiColor[0], t);
      const g = lerp(loColor[1], hiColor[1], t);
      const b = lerp(loColor[2], hiColor[2], t);
      return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
    }
  }
  return "#d50000";
}

/*
  Pitch-mix matchup color scale — separate from the zone heatmap.
  Royal blue (score 1, cold) → vibrant magenta (score 10, hot).
  score = 1-10 where 5 = league average (etb / league_etb * 5).
*/
const PITCH_SCALE: [number, [number, number, number]][] = [
  [1, [0x1e, 0x40, 0xaf]], // royal blue
  [3, [0x5b, 0x21, 0xb6]], // violet
  [5, [0x6d, 0x28, 0xd9]], // purple (league avg)
  [7, [0xc0, 0x26, 0xd3]], // orchid
  [10, [0xff, 0x2d, 0x95]], // magenta-hot
];

/** Map a 1-10 pitch-matchup score to a hex color (royal-blue → magenta). */
export function pitchHeat(score: number): string {
  const v = Math.max(1, Math.min(10, score));
  for (let i = 0; i < PITCH_SCALE.length - 1; i++) {
    const [lo, loC] = PITCH_SCALE[i];
    const [hi, hiC] = PITCH_SCALE[i + 1];
    if (v >= lo && v <= hi) {
      const t = (v - lo) / (hi - lo);
      return `#${toHex(lerp(loC[0], hiC[0], t))}${toHex(lerp(loC[1], hiC[1], t))}${toHex(lerp(loC[2], hiC[2], t))}`;
    }
  }
  return "#ff2d95";
}

/** Desaturated treatment for league-baseline / no-data cells. */
export function desaturate(hex: string, factor = 0.5): string {
  const m = hex.replace("#", "");
  const r = parseInt(m.slice(0, 2), 16);
  const g = parseInt(m.slice(2, 4), 16);
  const b = parseInt(m.slice(4, 6), 16);
  const gray = 0.3 * r + 0.59 * g + 0.11 * b;
  const mix = (c: number) => Math.round(c + (gray - c) * factor);
  return `#${toHex(mix(r))}${toHex(mix(g))}${toHex(mix(b))}`;
}
