import { formatPct } from "@/lib/format";

/*
  Tonight's HR odds for one hitter: a simple two-way split of the model's
  game-level homer probability. We don't try to call multi-HR games, so this
  is just P(homers) vs P(no HR) — the read fans actually want.
*/

export default function PlayerDistribution({ pGameHr }: { pGameHr: number }) {
  const pHr = Math.max(0, Math.min(1, pGameHr));
  const pNo = 1 - pHr;

  const segs = [
    { key: "hr", label: "Homers", pct: pHr, color: "#c8ff00" },
    { key: "no", label: "No HR", pct: pNo, color: "#1a2235" },
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
