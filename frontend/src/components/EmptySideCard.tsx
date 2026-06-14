/* Rendered when a single side has no lineup yet (error #2). */
export default function EmptySideCard({
  message,
  sp,
}: {
  message: string;
  sp: string;
}) {
  return (
    <div className="rounded-lg border border-dashed border-white/10 bg-card/50 p-6 text-center">
      <div className="font-mono text-sm text-text-muted">{message}</div>
      <div className="mt-2 font-sans text-xs text-text-muted">
        Probable starter: <span className="text-text-pri">{sp}</span>
      </div>
      <div className="mt-1 font-sans text-xs text-text-muted">
        Predictions appear once the lineup is posted.
      </div>
    </div>
  );
}
