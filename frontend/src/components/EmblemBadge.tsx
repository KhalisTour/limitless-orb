import type { Emblem } from "@/lib/emblems";
import EmblemGlyph from "./EmblemGlyph";

/*
  Perk crest: a circular medallion (Call-of-Duty-style) with the themed glyph
  in line art, a colored ring, a dark domed fill, and a neon glow. `compact`
  shows just the medallion (for crowded rows); the full form adds the label.
*/

function Medallion({ emblem, px }: { emblem: Emblem; px: number }) {
  const c = emblem.color;
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-full border"
      style={{
        width: px,
        height: px,
        color: c,
        borderColor: `${c}aa`,
        background: `radial-gradient(circle at 50% 35%, ${c}26, #0a0e17 78%)`,
        boxShadow: `0 0 6px ${c}66, inset 0 1px 1px ${c}40`,
      }}
    >
      <EmblemGlyph kind={emblem.kind} size={Math.round(px * 0.66)} />
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

  if (compact) {
    return (
      <span className={`inline-flex shrink-0 items-center gap-1.5 ${className}`}>
        {emblems.map((e) => (
          <span key={e.kind} title={e.title}>
            <Medallion emblem={e} px={27} />
          </span>
        ))}
      </span>
    );
  }

  return (
    <span className={`inline-flex flex-wrap items-center gap-1.5 ${className}`}>
      {emblems.map((e) => (
        <span
          key={e.kind}
          title={e.title}
          className="inline-flex items-center gap-1 rounded-full border py-0.5 pl-0.5 pr-2 font-mono text-[10px] font-bold uppercase tracking-wide"
          style={{
            color: e.color,
            borderColor: `${e.color}66`,
            background: `linear-gradient(180deg, ${e.color}1f, ${e.color}0a)`,
            boxShadow: `0 0 5px ${e.color}33`,
          }}
        >
          <Medallion emblem={e} px={18} />
          <span style={{ textShadow: `0 0 5px ${e.color}66` }}>{e.label}</span>
        </span>
      ))}
    </span>
  );
}
