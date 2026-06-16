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
  glyph: string;
  /** short uppercase label shown when there's room */
  label: string;
  /** full text for the title/tooltip */
  title: string;
  /** accent hex, used for the gradient + glow */
  color: string;
}

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
      glyph: "🚀",
      label: "LAUNCH PAD",
      title: `Hitter-friendly park (HR factor ${opts.parkFactor.toFixed(2)})`,
      color: "#ff2d6f", // neon-hot
    });
  }

  if (opts.platoonFactor != null && opts.platoonFactor >= PLATOON_MIN) {
    out.push({
      kind: "platoon",
      glyph: "⚔️",
      label: "PLATOON EDGE",
      title: `Favorable platoon matchup (x${opts.platoonFactor.toFixed(2)})`,
      color: "#00e5ff", // neon-cyan
    });
  }

  if (opts.barrelPctile != null && opts.barrelPctile >= BARREL_PCTILE_MIN) {
    out.push({
      kind: "barrel",
      glyph: "🔥",
      label: "BARREL HAWK",
      title: `Elite barrel rate — top ${100 - opts.barrelPctile}% of tonight's slate`,
      color: "#ffa726", // neon-amber
    });
  }

  return out;
}
