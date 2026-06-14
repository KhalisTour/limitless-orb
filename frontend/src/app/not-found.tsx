import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center py-24 text-center">
      <div className="font-mono text-3xl font-bold text-neon-lime">404</div>
      <p className="mt-2 font-sans text-sm text-text-muted">
        That game isn&apos;t on the board.
      </p>
      <Link
        href="/"
        className="mt-5 rounded-lg border border-white/10 px-4 py-2 font-mono text-sm text-text-pri hover:bg-hover"
      >
        Back to today&apos;s slate
      </Link>
    </div>
  );
}
