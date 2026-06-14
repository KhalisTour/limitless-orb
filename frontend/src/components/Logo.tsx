import Link from "next/link";

/* Orb AI wordmark + neon-chartreuse glyph (PART 5). ~32px tall. */
export default function Logo() {
  return (
    <Link href="/" className="flex items-center gap-2 select-none" aria-label="Orb AI home">
      <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
        <defs>
          <radialGradient id="orb-glyph" cx="40%" cy="35%" r="70%">
            <stop offset="0%" stopColor="#eaffa0" />
            <stop offset="55%" stopColor="#c8ff00" />
            <stop offset="100%" stopColor="#7da300" />
          </radialGradient>
        </defs>
        <circle cx="14" cy="14" r="11" fill="url(#orb-glyph)" />
        <circle cx="14" cy="14" r="11" fill="none" stroke="#c8ff00" strokeOpacity="0.5" strokeWidth="1.5" />
        {/* small orbit ring */}
        <ellipse
          cx="14"
          cy="14"
          rx="13"
          ry="5"
          fill="none"
          stroke="#00e5ff"
          strokeOpacity="0.7"
          strokeWidth="1"
          transform="rotate(-25 14 14)"
        />
      </svg>
      <span className="font-mono text-lg font-bold tracking-tight text-text-pri">
        Orb<span className="text-neon-lime"> AI</span>
      </span>
    </Link>
  );
}
