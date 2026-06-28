"use client";

/*
  Pitch-mix matchup — clustered bubble chart.

  Each bubble:
    size  = how often the pitcher throws that family (area ∝ usage)
    color = 1-10 matchup score for this batter (royal-blue cold → magenta hot)
    label = pitch family abbreviation + score + usage%

  Score: etb / league_etb_for_family * 5, clamped [1, 10]. 5 = league average.
  Bubbles are arranged with a one-pass spring layout so they cluster naturally
  (no D3 required — pure CSS absolute positions).
*/

import { useEffect, useRef, useState } from "react";
import type { FamilyMatchup } from "@/lib/types";
import { pitchHeat } from "@/lib/colors";

/* ----- helpers ----- */

const FAMILY_ABBREV: Record<string, string> = {
  four_seam: "4S",
  sinker: "SI",
  cutter: "CT",
  slider: "SL",
  change: "CH",
  curve: "CU",
  split: "FS",
  kn: "KN",
};

// Rough league averages for TB per PA by family (from 2024 Statcast).
// Used to convert raw etb → 1-10 index (5 = league avg for each family).
const LEAGUE_ETB: Record<string, number> = {
  four_seam: 0.360,
  sinker: 0.335,
  cutter: 0.330,
  slider: 0.295,
  change: 0.320,
  curve: 0.280,
  split: 0.300,
  kn: 0.310,
};

function tbScore(family: string, etb: number): number {
  const league = LEAGUE_ETB[family] ?? 0.330;
  const raw = (etb / league) * 5;
  return Math.max(1, Math.min(10, raw));
}

/* Weighted average score across the arsenal (usage-weighted). */
function netScore(families: FamilyMatchup[]): number {
  const total = families.reduce((s, f) => s + f.usage, 0);
  if (total <= 0) return 5;
  const ws = families.reduce((s, f) => s + tbScore(f.family, f.etb) * f.usage, 0);
  return ws / total;
}

/* ----- layout: simple force settle in a fixed canvas ----- */

interface Circle { x: number; y: number; r: number }

function settle(circles: Circle[], W: number, H: number, iters = 80): Circle[] {
  const cx = W / 2, cy = H / 2;
  const cs = circles.map((c) => ({ ...c }));
  for (let it = 0; it < iters; it++) {
    // spring toward center
    for (const c of cs) {
      c.x += (cx - c.x) * 0.04;
      c.y += (cy - c.y) * 0.04;
    }
    // repulsion between pairs
    for (let i = 0; i < cs.length; i++) {
      for (let j = i + 1; j < cs.length; j++) {
        const dx = cs[j].x - cs[i].x;
        const dy = cs[j].y - cs[i].y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const minD = cs[i].r + cs[j].r + 6;
        if (dist < minD) {
          const push = (minD - dist) / dist * 0.5;
          cs[i].x -= dx * push;
          cs[i].y -= dy * push;
          cs[j].x += dx * push;
          cs[j].y += dy * push;
        }
      }
    }
    // clamp to canvas
    for (const c of cs) {
      c.x = Math.max(c.r + 4, Math.min(W - c.r - 4, c.x));
      c.y = Math.max(c.r + 4, Math.min(H - c.r - 4, c.y));
    }
  }
  return cs;
}

/* ----- component ----- */

interface Props {
  families: FamilyMatchup[];
  /** canvas height in px (default 220) */
  height?: number;
}

export default function PitchBubbleChart({ families, height = 220 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(340);

  useEffect(() => {
    if (!containerRef.current) return;
    const obs = new ResizeObserver(() => {
      setWidth(containerRef.current!.clientWidth || 340);
    });
    obs.observe(containerRef.current);
    setWidth(containerRef.current.clientWidth || 340);
    return () => obs.disconnect();
  }, []);

  if (!families || families.length === 0) return null;

  const sorted = [...families].sort((a, b) => b.usage - a.usage);
  const maxUsage = sorted[0].usage;

  // Bubble radius: biggest bubble ~ 52px, others scale by sqrt(usage/max)
  const MAX_R = Math.min(52, width * 0.145);
  const MIN_R = 20;

  const raw: Circle[] = sorted.map((f, i) => ({
    x: width / 2 + (i % 3 - 1) * 90,
    y: height / 2 + (Math.floor(i / 3) - 0.5) * 80,
    r: Math.max(MIN_R, MAX_R * Math.sqrt(f.usage / maxUsage)),
  }));

  const placed = settle(raw, width, height);
  const net = netScore(sorted);
  const isEdge = net >= 5.8;
  const isFade = net <= 4.2;

  return (
    <div
      ref={containerRef}
      style={{ position: "relative", width: "100%", height }}
    >
      {placed.map((c, i) => {
        const f = sorted[i];
        const score = tbScore(f.family, f.etb);
        const color = pitchHeat(score);
        const abbrev = FAMILY_ABBREV[f.family] ?? f.family.slice(0, 2).toUpperCase();
        const usagePct = Math.round(f.usage * 100);
        const fontSize = Math.max(10, Math.min(22, c.r * 0.55));
        const labelSize = Math.max(8, c.r * 0.26);

        return (
          <div
            key={f.family}
            title={`${f.family}: score ${score.toFixed(1)}, xTB ${f.etb.toFixed(3)}, usage ${usagePct}%`}
            style={{
              position: "absolute",
              left: c.x - c.r,
              top: c.y - c.r,
              width: c.r * 2,
              height: c.r * 2,
              borderRadius: "9999px",
              background: color,
              boxShadow: `inset 0 0 ${c.r * 0.4}px rgba(255,255,255,0.15), 0 2px 12px rgba(0,0,0,0.4)`,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 1,
              cursor: "default",
              transition: "transform 0.15s",
            }}
            onMouseEnter={(e) => ((e.currentTarget as HTMLDivElement).style.transform = "scale(1.07)")}
            onMouseLeave={(e) => ((e.currentTarget as HTMLDivElement).style.transform = "scale(1)")}
          >
            {/* score */}
            <span
              style={{
                fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)",
                fontWeight: 800,
                fontSize,
                color: "#fff",
                lineHeight: 1,
                textShadow: "0 1px 3px rgba(0,0,0,0.6)",
              }}
            >
              {score.toFixed(score >= 9.95 ? 0 : 1)}
            </span>
            {/* family + usage */}
            {c.r >= 28 && (
              <span
                style={{
                  fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)",
                  fontWeight: 700,
                  fontSize: labelSize,
                  color: "rgba(255,255,255,0.75)",
                  lineHeight: 1,
                  letterSpacing: "0.04em",
                }}
              >
                {abbrev} {usagePct}%
              </span>
            )}
          </div>
        );
      })}

      {/* net score chip — bottom-right */}
      <div
        style={{
          position: "absolute",
          bottom: 8,
          right: 10,
          display: "flex",
          alignItems: "center",
          gap: 6,
          fontFamily: "var(--font-mono, 'JetBrains Mono', monospace)",
          fontSize: 11,
        }}
      >
        <span style={{ color: "rgba(154,160,166,0.8)" }}>net</span>
        <span
          style={{
            fontWeight: 700,
            color: isEdge ? "#c8ff00" : isFade ? "#ff2d6f" : "#e8eaed",
          }}
        >
          {net.toFixed(1)}
        </span>
        {isEdge && (
          <span style={{ color: "#c8ff00", fontWeight: 700, fontSize: 10 }}>▲ EDGE</span>
        )}
        {isFade && (
          <span style={{ color: "#ff2d6f", fontWeight: 700, fontSize: 10 }}>▼ FADE</span>
        )}
      </div>
    </div>
  );
}
