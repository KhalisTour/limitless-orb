"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { refreshGame } from "@/lib/api";

/*
  Both-sides-errored state (error #3) with a refresh button that handles all
  three /api/refresh body shapes (error #17):
    refreshed:true       -> router.refresh() to re-pull
    rate_limited:true    -> disable + countdown for retry_in_s
    refresh_failed:true  -> toast, keep showing stale
*/

export default function PendingLineupHero({
  gameId,
  awaySp,
  homeSp,
}: {
  gameId: number;
  awaySp: string;
  homeSp: string;
}) {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [cooldown, setCooldown] = useState(0);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (cooldown <= 0) return;
    const t = setInterval(() => setCooldown((c) => Math.max(0, c - 1)), 1000);
    return () => clearInterval(t);
  }, [cooldown]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  async function onRefresh() {
    setLoading(true);
    const res = await refreshGame(gameId);
    setLoading(false);
    if (!res.ok) {
      setToast("refresh failed, retry in 10 min");
      return;
    }
    const body = res.data;
    if (body.refreshed) {
      router.refresh();
    } else if (body.rate_limited) {
      setCooldown(body.retry_in_s ?? 600);
    } else if (body.refresh_failed) {
      setToast("refresh failed, retry in 10 min");
    }
  }

  const mm = Math.floor(cooldown / 60);
  const ss = String(cooldown % 60).padStart(2, "0");

  return (
    <div className="relative flex flex-col items-center justify-center rounded-xl border border-white/10 bg-card p-10 text-center">
      <div className="font-mono text-sm uppercase tracking-wide text-text-muted">
        Lineups not posted yet
      </div>
      <div className="mt-3 font-sans text-text-pri">
        {awaySp} <span className="text-text-muted">vs</span> {homeSp}
      </div>
      <p className="mt-2 max-w-sm font-sans text-xs text-text-muted">
        Both lineups are still pending. Predictions populate once the cards are
        official — usually a few hours before first pitch.
      </p>
      <button
        type="button"
        onClick={onRefresh}
        disabled={loading || cooldown > 0}
        className="mt-5 rounded-lg border border-neon-lime/50 bg-neon-lime/10 px-4 py-2 font-mono text-sm font-bold text-neon-lime transition-colors enabled:hover:bg-neon-lime/20 disabled:opacity-40"
      >
        {loading
          ? "Refreshing…"
          : cooldown > 0
            ? `Retry in ${mm}:${ss}`
            : "Refresh predictions"}
      </button>
      {toast && (
        <div className="absolute -bottom-3 translate-y-full rounded-md bg-neon-hot/20 px-3 py-1.5 font-mono text-xs text-neon-hot">
          {toast}
        </div>
      )}
    </div>
  );
}
