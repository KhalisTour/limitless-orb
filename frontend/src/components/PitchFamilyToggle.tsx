/* Pitch-family toggle (PART 6 §2). UI rendered but disabled in v1 — real
   toggle ships v1.5. All calls use pitch_family=all. */
const FAMILIES = ["All", "Fastball", "Breaking", "Offspeed"];

export default function PitchFamilyToggle() {
  return (
    <div className="flex items-center gap-2">
      <div className="flex gap-1 opacity-50">
        {FAMILIES.map((f) => (
          <span
            key={f}
            className={`cursor-not-allowed rounded-full border px-2.5 py-1 font-mono text-[11px] ${
              f === "All"
                ? "border-neon-lime/40 text-neon-lime"
                : "border-white/10 text-text-muted"
            }`}
          >
            {f}
          </span>
        ))}
      </div>
      <span className="font-mono text-[10px] uppercase tracking-wide text-text-muted">Coming soon</span>
    </div>
  );
}
