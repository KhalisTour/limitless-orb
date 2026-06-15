import type { OddsLine } from "@/lib/types";
import { formatPct } from "@/lib/format";

/*
  Betting edge display. `edge` = model probability − book's implied probability.
  Green when the model beats the book (+EV), muted/red when it doesn't.
  Renders nothing if there's no usable line (no fabricated data).
*/

function fmtAmerican(a: number | null): string {
  if (a === null) return "—";
  return a > 0 ? `+${a}` : `${a}`;
}

export function EdgeBadge({ line }: { line: OddsLine | undefined }) {
  if (!line || line.edge === null) return null;
  const positive = line.edge > 0;
  const cls = positive
    ? "text-neon-lime border-neon-lime/50 bg-neon-lime/10"
    : "text-text-muted border-text-muted/30 bg-text-muted/5";
  return (
    <span className={`shrink-0 rounded border px-1.5 py-0.5 font-mono text-[11px] ${cls}`} title="Model edge vs book">
      {positive ? "+" : ""}
      {(line.edge * 100).toFixed(1)}% edge
    </span>
  );
}

/* Full odds breakdown for an expanded card / hero. */
export function EdgeDetail({ line, book }: { line: OddsLine | undefined; book: string | null }) {
  if (!line || line.edge === null) return null;
  const positive = line.edge > 0;
  return (
    <div className="rounded-lg border border-white/5 bg-card p-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="font-mono text-[11px] uppercase tracking-wide text-text-muted">
          Edge {book ? `vs ${book}` : ""}
        </span>
        <span className={`font-mono text-sm font-bold ${positive ? "text-neon-lime" : "text-text-muted"}`}>
          {positive ? "+" : ""}
          {(line.edge * 100).toFixed(1)}%
        </span>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-text-muted">
        <span>line <span className="text-text-pri">{fmtAmerican(line.american)}</span></span>
        <span>implied <span className="text-text-pri">{line.implied !== null ? formatPct(line.implied) : "—"}</span></span>
        <span>model <span className="text-text-pri">{line.model_p !== null ? formatPct(line.model_p) : "—"}</span></span>
        {line.ev !== null && (
          <span>
            EV{" "}
            <span className={positive ? "text-neon-lime" : "text-text-pri"}>
              {line.ev >= 0 ? "+" : ""}${line.ev.toFixed(2)}
            </span>
          </span>
        )}
      </div>
    </div>
  );
}
