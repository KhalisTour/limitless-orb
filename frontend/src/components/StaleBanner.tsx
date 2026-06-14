/* Amber banner when serving a prior date's predictions (error #14). */
export default function StaleBanner({ date }: { date: string }) {
  return (
    <div className="w-full bg-neon-amber/15 border-y border-neon-amber/40 px-4 py-2 text-center">
      <span className="font-mono text-xs uppercase tracking-wide text-neon-amber">
        Stale — data from {date}
      </span>
    </div>
  );
}
