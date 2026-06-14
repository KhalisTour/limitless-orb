import { zoneColor } from "@/lib/colors";

/*
  MonteCarloBar (PART 3). Renders sim.team_hr_dist as horizontal bars,
  buckets 0..6. MUST parse-and-sort first (keys are stringified ints, out
  of order). Bucket fill via zoneColor() on a fake hr_pct scaled by bucket
  index. Cumulative "≥N" overlay in cyan. Label = expected HRs.
*/

export default function MonteCarloBar({ dist }: { dist: Record<string, number> }) {
  const sorted = Object.entries(dist)
    .map(([k, v]) => [parseInt(k, 10), v] as [number, number])
    .filter(([k]) => Number.isFinite(k))
    .sort((a, b) => a[0] - b[0]);

  const expected = sorted.reduce((acc, [k, p]) => acc + k * p, 0);
  const maxP = Math.max(...sorted.map(([, p]) => p), 0.001);
  const total = sorted.reduce((acc, [, p]) => acc + p, 0) || 1;

  // cumulative P(>= k)
  let running = 0;
  const cumAtLeast = new Map<number, number>();
  for (let i = sorted.length - 1; i >= 0; i--) {
    running += sorted[i][1];
    cumAtLeast.set(sorted[i][0], running / total);
  }

  return (
    <div className="rounded-lg border border-white/5 bg-card p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <span className="font-sans text-sm text-text-pri">Team HR simulation</span>
        <span className="font-mono text-sm text-neon-lime">
          {expected.toFixed(1)} <span className="text-[11px] text-text-muted">expected HRs tonight</span>
        </span>
      </div>
      <div className="space-y-1.5">
        {sorted.map(([k, p]) => {
          // fake hr_pct: scale bucket index 0..6 across the color scale
          const fill = zoneColor((k / 6) * 0.1);
          const widthPct = (p / maxP) * 100;
          const atLeast = cumAtLeast.get(k) ?? 0;
          return (
            <div key={k} className="flex items-center gap-2">
              <span className="w-10 shrink-0 font-mono text-xs text-text-muted">{k} HR</span>
              <div className="relative h-5 flex-1 overflow-hidden rounded bg-base">
                <div
                  className="h-full rounded"
                  style={{ width: `${widthPct}%`, backgroundColor: fill }}
                />
              </div>
              <span className="w-12 shrink-0 text-right font-mono text-xs text-text-pri">
                {(p * 100).toFixed(1)}%
              </span>
              <span
                className="w-14 shrink-0 text-right font-mono text-[11px]"
                style={{ color: "#00e5ff" }}
                title={`P(≥${k} HR)`}
              >
                ≥{(atLeast * 100).toFixed(0)}%
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
