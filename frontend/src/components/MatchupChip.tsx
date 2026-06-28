import type { TbPrediction } from "@/lib/types";

/*
  MatchupChip — the tensor's signed matchup delta as a glanceable pill.
  The HR ring already implies "good hitter"; this carries information the ring
  does NOT: whether THIS starter's pitch mix feeds or neutralizes the hitter's
  extra-base power. Hidden when the matchup is neutral so it only shows when it
  means something.
*/

// TB/PA deltas run ~±0.02 (sd ~0.009); show the chip past roughly 1 sd.
const THRESHOLD = 0.008;

export default function MatchupChip({
  tb,
  size = "sm",
}: {
  tb: TbPrediction | null;
  size?: "sm" | "md";
}) {
  const d = tb?.tensor_delta;
  if (d == null || Math.abs(d) < THRESHOLD) return null;

  const up = d > 0;
  const text = size === "sm" ? "text-[11px]" : "text-xs";
  const tone = up
    ? "text-neon-lime border-neon-lime/45 bg-neon-lime/10"
    : "text-neon-hot border-neon-hot/45 bg-neon-hot/10";

  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono font-bold tracking-wide ${text} ${tone}`}
      title={`${up ? "+" : ""}${d.toFixed(3)} xTB vs this pitcher's arsenal`}
    >
      <span className="text-[10px] leading-none">{up ? "▲" : "▼"}</span>
      {up ? "EDGE" : "FADE"}
    </span>
  );
}
