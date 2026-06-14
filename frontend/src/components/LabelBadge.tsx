import type { ProbLabel } from "@/lib/types";

/* ELITE/HIGH/MED/LOW descriptor — the primary read for non-technical fans.
   Raw % shown separately as a muted sub-label by callers (PART 2). */

const STYLES: Record<ProbLabel, string> = {
  ELITE: "text-neon-lime border-neon-lime/50 bg-neon-lime/10",
  HIGH: "text-neon-amber border-neon-amber/50 bg-neon-amber/10",
  MED: "text-neon-cyan border-neon-cyan/50 bg-neon-cyan/10",
  LOW: "text-text-muted border-text-muted/30 bg-text-muted/5",
};

export default function LabelBadge({
  label,
  size = "md",
}: {
  label: ProbLabel;
  size?: "sm" | "md";
}) {
  return (
    <span
      className={`inline-block rounded border px-2 py-0.5 font-mono font-bold tracking-wide ${
        size === "sm" ? "text-[11px]" : "text-xs"
      } ${STYLES[label]}`}
    >
      {label}
    </span>
  );
}
