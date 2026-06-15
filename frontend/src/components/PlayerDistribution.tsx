import { formatPct } from "@/lib/format";

/*
  Per-player HR outcome distribution for tonight, derived from the model's
  cumulative probabilities:
    P(0 HR)  = 1 - p_game_hr
    P(1 HR)  = p_game_hr - p_multi_hr
    P(2+ HR) = p_multi_hr
  Rendered as a 100% stacked bar so you can see where the probability mass
  sits at a glance.
*/

export default function PlayerDistribution({
  pGameHr,
  pMultiHr,
}: {
  pGameHr: number;
  pMultiHr: number;
}) {
  const p2 = Math.max(0, Math.min(1, pMultiHr));
  const p1 = Math.max(0, Math.min(1, pGameHr - pMultiHr));
  const p0 = Math.max(0, 1 - pGameHr);

  const segs = [
    { key: "0", label: "0 HR", pct: p0, color: "#1a2235" },
    { key: "1", label: "1 HR", pct: p1, color: "#c8ff00" },
    { key: "2", label: "2+ HR", pct: p2, color: "#ff2d6f" },
  ];

  return (
    <div>
      <div className="flex h-3 w-full overflow-hidden rounded-full">
        {segs.map((s) => (
          <div
            key={s.key}
            style={{ width: `${s.pct * 100}%`, backgroundColor: s.color }}
            title={`${s.label}: ${formatPct(s.pct)}`}
          />
        ))}
      </div>
      <div className="mt-1.5 flex justify-between font-mono text-[11px]">
        {segs.map((s) => (
          <span key={s.key} className="flex items-center gap-1">
            <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: s.color }} />
            <span className="text-text-muted">{s.label}</span>
            <span className="text-text-pri">{formatPct(s.pct)}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
