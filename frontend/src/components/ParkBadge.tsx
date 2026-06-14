/* Park-factor badge. onlyExtremes -> render only if >1.05 or <0.95. */
export default function ParkBadge({
  factor,
  onlyExtremes = false,
}: {
  factor: number | null;
  onlyExtremes?: boolean;
}) {
  if (factor === null || factor === undefined) return null;
  if (onlyExtremes && factor <= 1.05 && factor >= 0.95) return null;

  const hot = factor > 1.05;
  const color = hot ? "text-neon-hot border-neon-hot/40" : "text-neon-cyan border-neon-cyan/40";
  return (
    <span
      className={`shrink-0 rounded border px-1.5 py-0.5 font-mono text-[10px] ${color}`}
      title="Park HR factor"
    >
      PF {factor.toFixed(2)}
    </span>
  );
}
