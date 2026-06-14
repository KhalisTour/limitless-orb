"use client";

import { useRouter } from "next/navigation";
import { formatDate } from "@/lib/format";

/*
  DatePicker (PART 6 Page 1). prev/next arrows over the /api/dates list.
  dates arrive newest-first. Arrows disable at bounds. Selecting pushes
  to /?date=YYYY-MM-DD.
*/

export default function DatePicker({
  dates,
  current,
}: {
  dates: string[];
  current: string;
}) {
  const router = useRouter();
  const idx = dates.indexOf(current);
  // newest-first: index 0 is newest. "next" (newer) decreases index.
  const newer = idx > 0 ? dates[idx - 1] : null;
  const older = idx >= 0 && idx < dates.length - 1 ? dates[idx + 1] : null;

  const go = (d: string | null) => {
    if (!d) return;
    router.push(`/?date=${d}`);
  };

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => go(older)}
        disabled={!older}
        aria-label="Previous day"
        className="rounded-md border border-white/10 px-2 py-1 font-mono text-text-pri transition-colors enabled:hover:bg-hover disabled:opacity-30"
      >
        ‹
      </button>
      <span className="min-w-[7.5rem] text-center font-mono text-sm text-text-pri">
        {formatDate(current) ?? current}
      </span>
      <button
        type="button"
        onClick={() => go(newer)}
        disabled={!newer}
        aria-label="Next day"
        className="rounded-md border border-white/10 px-2 py-1 font-mono text-text-pri transition-colors enabled:hover:bg-hover disabled:opacity-30"
      >
        ›
      </button>
    </div>
  );
}
