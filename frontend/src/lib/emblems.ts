/*
  Card emblems — small "video-game" achievement badges surfaced on game and
  batter cards before you expand them. Each is derived purely from data the
  API already carries (park factor, platoon factor, Statcast barrel rate), so
  nothing is fabricated. Barrel uses the slate-relative percentile (top of the
  slate), not an absolute cutoff, so it stays meaningful night to night.
*/

export type EmblemKind = "launch_pad" | "platoon" | "barrel";

export interface Emblem {
  kind: EmblemKind;
  /** short uppercase label shown when there's room */
  label: string;
  /** full text for the title/tooltip */
  title: string;
  /** accent hex, used for the crest glyph, ring + glow */
  color: string;
}

/* Perk-crest accent colors (Call-of-Duty-style emblems). */
const COLOR = {
  park: "#c8ff00", // yellow-green — the stadium crest
  platoon: "#3b6fe6", // navy blue — the mitt-and-ball crest
  barrel: "#e0203a", // crimson — the cannon-and-bat crest
} as const;

/* Thresholds tuned so emblems stay rare enough to feel earned. */
const LAUNCH_PAD_MIN = 1.08; // park HR factor — clearly hitter-friendly
const PLATOON_MIN = 1.05; // favorable handedness matchup
const BARREL_PCTILE_MIN = 80; // top fifth of tonight's slate by barrel rate

export function emblemsFor(opts: {
  parkFactor?: number | null;
  platoonFactor?: number | null;
  barrelPctile?: number | null;
}): Emblem[] {
  const out: Emblem[] = [];

  if (opts.parkFactor != null && opts.parkFactor >= LAUNCH_PAD_MIN) {
    out.push({
      kind: "launch_pad",
      label: "LAUNCH PAD",
      title: `Hitter-friendly park (HR factor ${opts.parkFactor.toFixed(2)})`,
      color: COLOR.park,
    });
  }

  if (opts.platoonFactor != null && opts.platoonFactor >= PLATOON_MIN) {
    out.push({
      kind: "platoon",
      label: "PLATOON EDGE",
      title: `Favorable platoon matchup (x${opts.platoonFactor.toFixed(2)})`,
      color: COLOR.platoon,
    });
  }

  if (opts.barrelPctile != null && opts.barrelPctile >= BARREL_PCTILE_MIN) {
    out.push({
      kind: "barrel",
      label: "BARREL HAWK",
      title: `Elite barrel rate — top ${100 - opts.barrelPctile}% of tonight's slate`,
      color: COLOR.barrel,
    });
  }

  return out;
}
