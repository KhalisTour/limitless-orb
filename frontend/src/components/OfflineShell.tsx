/* Static fallback when the backend is unreachable during SSR (error #1). */
export default function OfflineShell() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-6 py-24 text-center">
      <div className="font-mono text-lg font-bold text-neon-amber">Orb AI is catching its breath</div>
      <p className="mt-3 max-w-sm font-sans text-sm text-text-muted">
        We couldn&apos;t reach today&apos;s prediction feed. This is usually
        brief — the pipeline runs daily and the link will refresh on its own.
      </p>
      <p className="mt-4 font-mono text-xs text-text-muted">Check back in a few minutes.</p>
    </div>
  );
}
