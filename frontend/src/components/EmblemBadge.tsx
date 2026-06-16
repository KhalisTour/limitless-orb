import type { Emblem } from "@/lib/emblems";

/*
  Video-game emblem chip: a glyph in a colored, shadowed pill with a gradient
  fill, neon glow and an inset top highlight so it reads like a game crest.
  `compact` drops the text label to just the glyph (for crowded rows); the
  full title still rides on the tooltip for accessibility.
*/

function EmblemChip({ emblem, compact }: { emblem: Emblem; compact?: boolean }) {
  const c = emblem.color;
  return (
    <span
      title={emblem.title}
      className={`inline-flex shrink-0 items-center gap-1 rounded-md border font-mono font-bold uppercase tracking-wide ${
        compact ? "px-1 py-0.5 text-[11px]" : "px-1.5 py-0.5 text-[10px]"
      }`}
      style={{
        color: c,
        borderColor: `${c}66`,
        background: `linear-gradient(180deg, ${c}26, ${c}0d)`,
        boxShadow: `0 0 8px ${c}40, inset 0 1px 0 ${c}33`,
        textShadow: `0 0 6px ${c}80`,
      }}
    >
      <span aria-hidden>{emblem.glyph}</span>
      {!compact && <span>{emblem.label}</span>}
    </span>
  );
}

export default function EmblemRow({
  emblems,
  compact = false,
  className = "",
}: {
  emblems: Emblem[];
  compact?: boolean;
  className?: string;
}) {
  if (emblems.length === 0) return null;
  return (
    <span className={`inline-flex flex-wrap items-center gap-1 ${className}`}>
      {emblems.map((e) => (
        <EmblemChip key={e.kind} emblem={e} compact={compact} />
      ))}
    </span>
  );
}
