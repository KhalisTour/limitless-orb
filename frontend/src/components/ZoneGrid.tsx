"use client";

import { zoneColor, desaturate } from "@/lib/colors";

/*
  ZoneGrid (PART 3). Canonical 13-zone strike grid, catcher's perspective.
  viewBox 0 0 300 340 (do not change). String keys only — a numeric key
  silently renders uncolored, so we assert in dev.

  valueKey selects which numeric field to color/label from each cell.
  Missing cell -> desaturated league-baseline color, no number (never a gap).
*/

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

const COORDS: Record<string, Box> = {
  "1": { x: 90, y: 70, w: 60, h: 55 },
  "2": { x: 150, y: 70, w: 60, h: 55 },
  "3": { x: 210, y: 70, w: 60, h: 55 },
  "4": { x: 90, y: 125, w: 60, h: 55 },
  "5": { x: 150, y: 125, w: 60, h: 55 },
  "6": { x: 210, y: 125, w: 60, h: 55 },
  "7": { x: 90, y: 180, w: 60, h: 55 },
  "8": { x: 150, y: 180, w: 60, h: 55 },
  "9": { x: 210, y: 180, w: 60, h: 55 },
  // out-of-zone CORNER blocks (not strips)
  "11": { x: 65, y: 45, w: 85, h: 25 },
  "12": { x: 210, y: 45, w: 85, h: 25 },
  "13": { x: 65, y: 235, w: 85, h: 25 },
  "14": { x: 210, y: 235, w: 85, h: 25 },
};

const ZONE_ORDER = Object.keys(COORDS);

export type ZoneValueKey = "hr_pct" | "pitcher_tendency" | "danger_pct";
/* Accept concrete cell interfaces (ZoneCell, MatchupZoneCell) structurally:
   only the keys we read need to line up; extra fields are fine. */
export type CellLike = { n?: number } & Partial<Record<ZoneValueKey, number>>;

interface Props {
  zones: Record<string, CellLike>;
  valueKey: ZoneValueKey;
  hottestZone?: string | null;
  size?: number;
  showCounts?: boolean;
  showSilhouette?: boolean;
  baseline?: Record<string, CellLike>;
  /** player_found:false -> render baseline grid in muted colors. */
  desaturateAll?: boolean;
  dimmed?: boolean;
}

export default function ZoneGrid({
  zones,
  valueKey,
  hottestZone = null,
  size = 300,
  showCounts = false,
  showSilhouette = true,
  baseline,
  desaturateAll = false,
  dimmed = false,
}: Props) {
  if (process.env.NODE_ENV !== "production") {
    for (const k of Object.keys(zones)) {
      if (typeof k !== "string") {
        throw new Error(`ZoneGrid: numeric zone key reached lookup (${k}). Keys must be strings.`);
      }
    }
  }

  const height = (size * 340) / 300;

  return (
    <svg
      width={size}
      height={height}
      viewBox="0 0 300 340"
      className={dimmed ? "opacity-50 transition-opacity" : "transition-opacity"}
      role="img"
      aria-label="Strike zone heat map"
    >
      {/* batter silhouette, right side, static */}
      {showSilhouette && (
        <path
          d="M278 95 q8 -14 4 -26 a9 9 0 1 0 -18 0 q-4 12 4 26 l-4 70 l6 0 l3 -52 l3 52 l6 0 z"
          fill="#9aa0a6"
          fillOpacity="0.18"
        />
      )}

      {ZONE_ORDER.map((key) => {
        const box = COORDS[key];
        const cell = zones[key];
        const baseCell = baseline?.[key];
        const hasData = cell && cell[valueKey] !== undefined && cell[valueKey] !== null;

        let fill: string;
        let label: string | null = null;
        let muted = false;

        if (hasData) {
          const v = cell![valueKey] as number;
          fill = desaturateAll ? desaturate(zoneColor(v)) : zoneColor(v);
          label = (v * 100).toFixed(1);
          if (desaturateAll) muted = true;
        } else if (baseCell && baseCell[valueKey] !== undefined) {
          fill = desaturate(zoneColor(baseCell[valueKey] as number), 0.65);
          muted = true;
        } else if (baseCell && baseCell.hr_pct !== undefined) {
          fill = desaturate(zoneColor(baseCell.hr_pct), 0.65);
          muted = true;
        } else {
          fill = "#1a2235";
          muted = true;
        }

        const isHot = hottestZone === key && hasData;
        const cx = box.x + box.w / 2;
        const cy = box.y + box.h / 2;
        const n = cell?.n;

        return (
          <g key={key}>
            <rect
              className="orb-zone-fill"
              x={box.x}
              y={box.y}
              width={box.w}
              height={box.h}
              rx={3}
              fill={isHot ? "#c8ff00" : fill}
              stroke={isHot ? "#c8ff00" : "#00e5ff"}
              strokeOpacity={isHot ? 1 : 0.25}
              strokeWidth={isHot ? 2 : 1}
            />
            {isHot && (
              <rect
                className="orb-pulse"
                x={box.x}
                y={box.y}
                width={box.w}
                height={box.h}
                rx={3}
                fill="#c8ff00"
              />
            )}
            {label !== null && !muted && (
              <text
                x={cx}
                y={cy}
                textAnchor="middle"
                dominantBaseline="central"
                className="font-mono font-bold"
                fontSize={box.h > 30 ? 13 : 10}
                fill={isHot ? "#0a0e17" : "#0a0e17"}
                style={{ paintOrder: "stroke", stroke: "#ffffffaa", strokeWidth: 0.6 }}
              >
                {label}
              </text>
            )}
            {showCounts && n !== undefined && n < 30 && (
              <text
                x={box.x + box.w - 3}
                y={box.y + box.h - 3}
                textAnchor="end"
                className="font-mono"
                fontSize={7}
                fill="#0a0e17"
                fillOpacity={0.6}
              >
                n={n}
              </text>
            )}
          </g>
        );
      })}

      {/* home plate pentagon */}
      <polygon
        points="120,300 180,300 180,315 150,328 120,315"
        fill="none"
        stroke="#9aa0a6"
        strokeOpacity="0.35"
        strokeWidth="1.5"
      />
    </svg>
  );
}
