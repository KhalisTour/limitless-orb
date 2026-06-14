"use client";

import { useEffect, useRef, useState } from "react";
import type { TrajectoryResponse } from "@/lib/types";
import { isTrajectoryVisible, isSingleAxisFallback } from "@/lib/trajectory";

/*
  TrajectoryArc (PART 3). viewBox 0 0 500 200, ground at y=190.
  Hide rules (error #10/#11):
    found:false               -> render nothing
    used_key single-axis diff -> arc + dim "fallback" caption
    used_key both-axis diff    -> render nothing
*/

function catmullRomPath(points: [number, number][]): string {
  if (points.length === 0) return "";
  if (points.length === 1) return `M ${points[0][0]} ${points[0][1]}`;
  let d = `M ${points[0][0]} ${points[0][1]}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p0 = points[i - 1] ?? points[i];
    const p1 = points[i];
    const p2 = points[i + 1];
    const p3 = points[i + 2] ?? p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C ${c1x} ${c1y}, ${c2x} ${c2y}, ${p2[0]} ${p2[1]}`;
  }
  return d;
}

export default function TrajectoryArc({
  data,
  requestedZone,
  requestedFamily,
}: {
  data: TrajectoryResponse;
  requestedZone: string;
  requestedFamily: string;
}) {
  const pathRef = useRef<SVGPathElement>(null);
  const [revealed, setRevealed] = useState(false);
  const [len, setLen] = useState(0);

  const visible = isTrajectoryVisible(data, requestedZone, requestedFamily);
  const singleDiff = isSingleAxisFallback(data, requestedZone, requestedFamily);

  useEffect(() => {
    if (!visible) return;
    const el = pathRef.current;
    if (!el) return;
    const total = el.getTotalLength();
    setLen(total);
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setRevealed(true);
          io.disconnect();
        }
      },
      { threshold: 0.2 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [visible, data.used_key]);

  if (!visible) return null;

  const xScale = 480 / data.distance_ft;
  const yScale = 180 / data.apex_ft;
  const mapped: [number, number][] = data.trajectory.map(([xf, yf]) => [
    xf * xScale + 10,
    190 - yf * yScale,
  ]);

  const d = catmullRomPath(mapped);

  // label anchor points
  const contact = mapped[0];
  const landing = mapped[mapped.length - 1];
  let apexIdx = 0;
  for (let i = 1; i < mapped.length; i++) {
    if (mapped[i][1] < mapped[apexIdx][1]) apexIdx = i;
  }
  const apex = mapped[apexIdx];

  return (
    <div className="rounded-lg border border-white/5 bg-card p-4">
      <svg width="100%" viewBox="0 0 500 200" className="overflow-visible">
        <defs>
          <linearGradient id="arc-grad" x1="0" y1="1" x2="1" y2="0">
            <stop offset="0%" stopColor="#ffa726" />
            <stop offset="55%" stopColor="#c8ff00" />
            <stop offset="100%" stopColor="#00e5ff" />
          </linearGradient>
        </defs>

        {/* ground line */}
        <line x1="0" y1="190" x2="500" y2="190" stroke="#ffffff22" strokeWidth="1" />

        <path
          ref={pathRef}
          className="orb-arc"
          d={d}
          fill="none"
          stroke="url(#arc-grad)"
          strokeWidth="3"
          strokeLinecap="round"
          strokeDasharray={len || undefined}
          strokeDashoffset={len ? (revealed ? 0 : len) : undefined}
        />

        {/* contact */}
        <circle cx={contact[0]} cy={contact[1]} r="3" fill="#ffa726" />
        <text x={contact[0] + 4} y={contact[1] - 6} className="font-mono" fontSize="11" fill="#ffa726">
          EV {data.median_ev.toFixed(0)} MPH
        </text>

        {/* apex */}
        <circle cx={apex[0]} cy={apex[1]} r="3" fill="#c8ff00" />
        <text x={apex[0]} y={apex[1] - 8} textAnchor="middle" className="font-mono" fontSize="11" fill="#c8ff00">
          LA {data.median_la.toFixed(0)}° · {data.apex_ft.toFixed(0)}ft
        </text>

        {/* landing */}
        <circle cx={landing[0]} cy={landing[1]} r="3" fill="#00e5ff" />
        <text x={landing[0]} y={landing[1] - 8} textAnchor="end" className="font-mono" fontSize="11" fill="#00e5ff">
          {data.distance_ft.toFixed(0)}ft
        </text>
      </svg>

      {singleDiff && (
        <p className="mt-2 font-mono text-[11px] text-text-muted opacity-70">
          fallback: {data.used_key}
        </p>
      )}
    </div>
  );
}
