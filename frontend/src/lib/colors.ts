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
