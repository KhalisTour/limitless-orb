import { getAccuracy } from "@/lib/api";
import { formatPct } from "@/lib/format";
import type { AccuracyCall } from "@/lib/types";
import OfflineShell from "@/components/OfflineShell";

export const revalidate = 3600;

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-white/5 bg-card px-3 py-4 text-center">
      <div className="font-mono text-2xl font-bold text-text-pri">{value}</div>
      <div className="mt-1 font-mono text-[11px] uppercase text-text-muted">{label}</div>
    </div>
  );
}

function CallCard({ call, tone }: { call: AccuracyCall; tone: "good" | "bad" }) {
  const border = tone === "good" ? "border-neon-lime/40" : "border-neon-hot/40";
  return (
    <div className={`rounded-lg border ${border} bg-card p-3`}>
      <div className="font-sans text-sm text-text-pri">{call.hitter}</div>
      <div className="font-mono text-[11px] text-text-muted">vs {call.opp_pitcher}</div>
      <div className="mt-1 flex items-center justify-between font-mono text-[11px]">
        <span className="text-text-muted">{call.date}</span>
        <span className="text-text-pri">{formatPct(call.p_per_pa)}</span>
      </div>
    </div>
  );
}

export default async function AccuracyPage() {
  const res = await getAccuracy(30);
  if (!res.ok) return <OfflineShell />;
  const a = res.data;

  if (a.status === "insufficient_data") {
    return (
      <div className="flex flex-col items-center justify-center py-20 text-center">
        <h1 className="font-sans text-xl font-semibold text-text-pri">Accuracy</h1>
        <p className="mt-3 max-w-sm font-sans text-sm text-text-muted">
          Tracking {a.n_predictions} scored predictions so far. Need 50+ scored
          predictions for full calibration.
        </p>
      </div>
    );
  }

  const maxRate = Math.max(
    ...a.calibration_bins.flatMap((b) => [b.predicted_avg, b.actual_rate]),
    0.01,
  );

  return (
    <div>
      <header className="mb-4">
        <h1 className="font-sans text-xl font-semibold text-text-pri">Model Accuracy</h1>
        <p className="mt-0.5 font-mono text-xs text-text-muted">Last 30 days</p>
      </header>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatCard label="AUC" value={a.auc.toFixed(3)} />
        <StatCard label="Brier" value={a.brier.toFixed(3)} />
        <StatCard label="Log loss" value={a.log_loss.toFixed(3)} />
        <StatCard label="Predictions" value={String(a.n_predictions)} />
      </div>

      {/* Brier alone reads as "small number, good model". It only means
          anything next to what predicting the league rate for every hitter
          would have scored, so show both and say which won. */}
      {a.baseline_brier !== undefined && (
        <p className="mt-2 font-mono text-xs text-text-muted">
          Predicted {formatPct(a.rate_predicted)} vs {formatPct(a.rate_actual)} actual ·{" "}
          {a.beats_baseline
            ? `beats a flat league-rate forecast (${a.baseline_brier.toFixed(3)} Brier)`
            : `does NOT beat a flat league-rate forecast (${a.baseline_brier.toFixed(3)} Brier)`}
        </p>
      )}
      {/* Don't let a model change read as a model failure: after one, this
          window is still mostly the previous model's predictions. */}
      {a.current_generation_only === false && (
        <p className="mt-1 font-mono text-xs text-text-muted/80">
          Includes predictions from an earlier model version — not yet a read on
          the current one.
        </p>
      )}

      <section className="mt-6">
        <h2 className="mb-3 font-mono text-xs uppercase tracking-wide text-text-muted">
          Calibration
        </h2>
        <div className="rounded-lg border border-white/5 bg-card p-4">
          <div className="flex items-end gap-4" style={{ height: 160 }}>
            {a.calibration_bins.map((b) => (
              <div key={b.bin} className="flex flex-1 flex-col items-center justify-end gap-1">
                <div className="flex h-full w-full items-end justify-center gap-1">
                  <div
                    className="w-3 rounded-t"
                    style={{
                      height: `${(b.predicted_avg / maxRate) * 100}%`,
                      backgroundColor: "#00e5ff",
                    }}
                    title={`predicted ${formatPct(b.predicted_avg)}`}
                  />
                  <div
                    className="w-3 rounded-t"
                    style={{
                      height: `${(b.actual_rate / maxRate) * 100}%`,
                      backgroundColor: "#c8ff00",
                    }}
                    title={`actual ${formatPct(b.actual_rate)}`}
                  />
                </div>
                <span className="font-mono text-[10px] text-text-muted">{b.bin}</span>
              </div>
            ))}
          </div>
          <div className="mt-3 flex justify-center gap-4 font-mono text-[11px]">
            <span className="text-neon-cyan">■ predicted</span>
            <span className="text-neon-lime">■ actual</span>
          </div>
        </div>
      </section>

      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <section>
          <h2 className="mb-2 font-mono text-xs uppercase tracking-wide text-neon-lime">
            Best calls
          </h2>
          <div className="space-y-2">
            {a.best_calls.map((c, i) => (
              <CallCard key={i} call={c} tone="good" />
            ))}
          </div>
        </section>
        <section>
          <h2 className="mb-2 font-mono text-xs uppercase tracking-wide text-neon-hot">
            Worst misses
          </h2>
          <div className="space-y-2">
            {a.worst_misses.map((c, i) => (
              <CallCard key={i} call={c} tone="bad" />
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
