"use client";

import { useState } from "react";

/*
  Real MLB headshot by batter_id (MLBAM person id), with a graceful gradient
  + initials fallback for rookies (batter_id null) or missing photos.
  Uses a plain <img> so we don't need next/image remotePatterns config.
*/

function hashHue(seed: string): number {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) % 360;
  return h;
}

function initialsOf(name: string): string {
  return name
    .split(/\s+/)
    .map((w) => w[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

export default function Headshot({
  batterId,
  name,
  size = 48,
}: {
  batterId: string | null;
  name: string;
  size?: number;
}) {
  const [errored, setErrored] = useState(false);
  const hue = hashHue(batterId ?? name);
  const gradient = `linear-gradient(135deg, hsl(${hue} 58% 28%), hsl(${(hue + 40) % 360} 52% 16%))`;
  const showImg = batterId !== null && !errored;
  const spot = size >= 96 ? 240 : size >= 64 ? 120 : 90;

  return (
    <div
      className="relative shrink-0 overflow-hidden rounded-full border border-white/10"
      style={{ width: size, height: size, background: gradient }}
    >
      {showImg ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={`https://midfield.mlbstatic.com/v1/people/${batterId}/spots/${spot}`}
          alt={name}
          width={size}
          height={size}
          loading="lazy"
          onError={() => setErrored(true)}
          className="h-full w-full object-cover object-top"
        />
      ) : (
        <span
          className="absolute inset-0 flex items-center justify-center font-mono font-bold text-text-pri/90"
          style={{ fontSize: size * 0.32 }}
        >
          {initialsOf(name)}
        </span>
      )}
    </div>
  );
}
