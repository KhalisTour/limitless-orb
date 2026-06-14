import type { HitterComponents } from "@/lib/types";
import { formatPct } from "@/lib/format";

/*
  Model VIEWS, not contributions (PART 6 §5). Three independent per-PA
  probabilities + the ensemble. The point is DISAGREEMENT — these do NOT
  sum to p_per_pa. Each bar width = value / MAX_P (same 0.10 ceiling).
*/

const MAX_P = 0.1;

function Bar({
  label,
  value,
  color,
  bold = false,
}: {
  label: string;
  value: number;
  color: string;
  bold?: boolean;
}) {
  const w = Math.min(100, (value / MAX_P) * 100);
  return (
    <div className="flex items-center gap-3">
      <span className={`w-20 shrink-0 font-mono text-xs ${bold ? "font-bold text-text-pri" : "text-text-muted"}`}>
        {label}
      </span>
      <div className="h-3 flex-1 overflow-hidden rounded bg-base">
        <div className="h-full rounded" style={{ width: `${w}%`, backgroundColor: color }} />
      </div>
      <span className={`w-14 shrink-0 text-right font-mono text-xs ${bold ? "font-bold text-text-pri" : "text-text-muted"}`}>
        {formatPct(value, 2)}
      </span>
    </div>
  );
}

export default function ModelViews({
  components,
  ensemble,
}: {
  components: HitterComponents;
  ensemble: number;
}) {
  return (
    <div className="rounded-lg border border-white/5 bg-card p-4">
      <div className="space-y-2">
        <Bar label="Logistic" value={components.logistic} color="#00e5ff" />
        <Bar label="Matchup" value={components.matchup} color="#ffa726" />
        <Bar label="Linear" value={components.linear} color="#c8ff00" />
        <div className="my-1 border-t border-white/10" />
        <Bar label="Ensemble" value={ensemble} color="#ff2d6f" bold />
      </div>
      <p className="mt-3 font-sans text-xs text-text-muted">
        How each model sees this matchup. The ensemble (bottom) is what we
        trade on. Disagreement between models is information.
      </p>
    </div>
  );
}
