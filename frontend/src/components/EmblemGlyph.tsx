import type { EmblemKind } from "@/lib/emblems";

/*
  Perk-crest glyphs (Call-of-Duty-style emblems), drawn as line art in a
  24×24 box so they sit inside a circular medallion. Stroke is currentColor,
  so the medallion's theme color flows straight through.

    launch_pad → a ballpark (tiered bowl + light towers)
    platoon    → a fielder's mitt cradling a ball
    barrel     → a cannon crossed with a bat, muzzle blast firing
*/

export default function EmblemGlyph({
  kind,
  size = 13,
}: {
  kind: EmblemKind;
  size?: number;
}) {
  const common = {
    width: size,
    height: size,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.6,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };

  if (kind === "launch_pad") {
    // Stadium, side view: a domed grandstand over a field line, tiered seating
    // arc inside, and a pennant at the peak. Reads as an arena, not a face.
    return (
      <svg {...common} aria-hidden>
        <path d="M3.5 15 Q12 4.5 20.5 15" />
        <path d="M6 15 Q12 9 18 15" />
        <path d="M4 15.4 H20" />
        <path d="M12 6 V3.2" />
        <path d="M12 3.2 L15 4.4 L12 5.6 Z" fill="currentColor" stroke="none" />
      </svg>
    );
  }

  if (kind === "platoon") {
    // Fielder's mitt (pocket + thumb + finger ridges) cradling a stitched ball.
    return (
      <svg {...common} aria-hidden>
        <path d="M7 9.5 C5 11 5 16 8 17.5 C11 19 16 18.5 17.5 15.5 C18.6 13.3 18 10.8 16 9.6" />
        <path d="M8.2 9.2 L8.6 6.8" />
        <path d="M11 8.2 L11.2 6" />
        <path d="M13.8 8.4 L14.4 6.4" />
        <circle cx="12.6" cy="13" r="2.5" />
        <path d="M11.4 11.6 Q12.6 13 11.8 14.4" />
      </svg>
    );
  }

  // barrel: cannon (one diagonal) crossed with a bat (other diagonal), firing.
  return (
    <svg {...common} aria-hidden>
      {/* bat: handle bottom-left to barrel top-right, with a knob */}
      <path d="M7 17.5 L15.5 9" strokeWidth="2.2" />
      <circle cx="6.6" cy="17.9" r="1.1" fill="currentColor" stroke="none" />
      {/* cannon tube: top-left to bottom-right */}
      <path d="M8.5 9 L15.5 16" strokeWidth="2.6" />
      <circle cx="16.4" cy="16.8" r="1.6" />
      {/* muzzle blast at the cannon's mouth (top-left) */}
      <path d="M8.5 9 L6.2 8" />
      <path d="M8.5 9 L7.4 11.2" />
      <path d="M8.5 9 L6.4 10" />
    </svg>
  );
}
