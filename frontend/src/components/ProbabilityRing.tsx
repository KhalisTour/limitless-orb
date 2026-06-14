"use client";

import { useEffect, useRef, useState } from "react";
import { zoneColor } from "@/lib/colors";

/*
  ProbabilityRing (PART 3). Three sizes 40/60/100. MAX_P = 0.10.
  Fill via stroke-dashoffset CSS transition on IntersectionObserver entry
  (NOT Framer Motion — GPU-composited CSS is faster). Color interpolates
  the zone scale using p_per_pa.
*/

const MAX_P = 0.1;

const GEOM = {
  40: { r: 20, stroke: 4 },
  60: { r: 27, stroke: 5 },
  100: { r: 45, stroke: 7 },
} as const;

export type RingSize = keyof typeof GEOM;

export default function ProbabilityRing({
  value,
  size = 60,
  capLabel = true,
}: {
  value: number;
  size?: RingSize;
  capLabel?: boolean;
}) {
  const { r, stroke } = GEOM[size];
  const dim = size;
  const cx = dim / 2;
  const C = 2 * Math.PI * r;
  const capped = Math.min(value, MAX_P);
  const frac = capped / MAX_P;
  const targetOffset = C * (1 - frac);
  const over = value >= MAX_P;

  const color = zoneColor(value);

  const ref = useRef<SVGCircleElement>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setVisible(true);
          io.disconnect();
        }
      },
      { threshold: 0.2 },
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  const pctText = `${(value * 100).toFixed(0)}%${over && capLabel ? "+" : ""}`;
  const fontSize = size === 40 ? 10 : size === 60 ? 14 : 22;

  return (
    <svg width={dim} height={dim} viewBox={`0 0 ${dim} ${dim}`} className="shrink-0">
      <circle cx={cx} cy={cx} r={r} fill="none" stroke="#1a2235" strokeWidth={stroke} />
      <circle
        ref={ref}
        className="orb-ring-progress"
        cx={cx}
        cy={cx}
        r={r}
        fill="none"
        stroke={color}
        strokeWidth={stroke}
        strokeLinecap="round"
        strokeDasharray={C}
        strokeDashoffset={visible ? targetOffset : C}
        transform={`rotate(-90 ${cx} ${cx})`}
      />
      <text
        x={cx}
        y={cx}
        textAnchor="middle"
        dominantBaseline="central"
        className="font-mono font-bold"
        fontSize={fontSize}
        fill={color}
      >
        {pctText}
      </text>
    </svg>
  );
}
