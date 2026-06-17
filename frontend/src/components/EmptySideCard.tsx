/* Rendered when a single side has no lineup yet (error #2). */

function _friendlyMessage(raw: string): string {
  const r = raw.toLowerCase();
  if (r.includes("no") && r.includes("lineup")) return "Lineup not posted yet.";
  if (r.includes("no probable pitcher") || r.includes("no pitcher")) return "Starting pitcher not confirmed yet.";
  if (r.includes("lineup.csv")) return "Lineup not posted yet.";
  return "Lineup not available yet.";
}

export default function EmptySideCard({
  message,
  sp,
}: {
  message: string;
  sp: string;
}) {
  return (
    <div className="rounded-lg border border-dashed border-white/10 bg-card/50 p-6 text-center">
      <div className="font-mono text-sm text-text-muted">{_friendlyMessage(message)}</div>
      <div className="mt-2 font-sans text-xs text-text-muted">
        Probable starter: <span className="text-text-pri">{sp}</span>
      </div>
      <div className="mt-1 font-sans text-xs text-text-muted">
        Predictions appear once the lineup is posted.
      </div>
    </div>
  );
}
