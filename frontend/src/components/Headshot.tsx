"use client";

import { useCallback, useEffect, useRef, useState } from "react";

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
  const imgRef = useRef<HTMLImageElement | null>(null);

  // The <img> is server-rendered, so the browser starts (and can finish)
  // loading it before React hydrates and attaches onError. An error that has
  // already fired is gone — the handler never runs and the broken image sits
  // there showing its alt text, which is the player's full name spilling out of
  // a 48px circle. That is exactly the case the initials fallback exists for,
  // and it was the only case where it never worked. Re-check on mount: a
  // finished image with zero natural width is a failed image.
  const check = useCallback((el: HTMLImageElement | null) => {
    if (el && el.complete && el.naturalWidth === 0) setErrored(true);
  }, []);
  useEffect(() => {
    check(imgRef.current);
  }, [check, batterId]);

  const hue = hashHue(batterId ?? name);
  const gradient = `linear-gradient(135deg, hsl(${hue} 58% 28%), hsl(${(hue + 40) % 360} 52% 16%))`;
  const showImg = batterId !== null && !errored;
  const spot = size >= 96 ? 240 : size >= 64 ? 120 : 90;

  return (
    <div
      className="relative shrink-0 overflow-hidden rounded-full border border-white/10"
      style={{ width: size, height: size, background: gradient }}
    >
      {/* Initials sit underneath the photo, so a failed image reveals them
          instead of a blank circle even before the error is noticed. */}
      <span
        className="absolute inset-0 flex items-center justify-center font-mono font-bold text-text-pri/90"
        style={{ fontSize: size * 0.32 }}
        aria-hidden="true"
      >
        {initialsOf(name)}
      </span>
      {showImg ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          ref={(el) => {
            imgRef.current = el;
            check(el);
          }}
          src={`https://midfield.mlbstatic.com/v1/people/${batterId}/spots/${spot}`}
          // Empty alt: the initials underneath already carry the name, and a
          // broken image should reveal them rather than paint the name across
          // the avatar. The card's own text is the accessible label.
          alt=""
          width={size}
          height={size}
          loading="lazy"
          onError={() => setErrored(true)}
          onLoad={(e) => {
            if (e.currentTarget.naturalWidth === 0) setErrored(true);
          }}
          className="h-full w-full object-cover object-top"
        />
      ) : null}
    </div>
  );
}
