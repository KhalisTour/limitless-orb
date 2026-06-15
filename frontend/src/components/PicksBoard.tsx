"use client";

import { useEffect, useMemo, useState } from "react";
import { getResults } from "@/lib/api";
import { formatPct } from "@/lib/format";
import {
  MAX_PICKS,
  computeStats,
  expectedHits,
  isSlateLocked,
  loadStore,
  poissonBinomial,
  saveStore,
  scoreDay,
  type DaySlate,
  type Pick,
  type PicksStore,
} from "@/lib/picks";
import Headshot from "./Headshot";

/*
  Daily HR Pick'em (points + streaks, anonymous/on-device). Pick up to 5
  hitters to homer; lock the slate; it grades against /api/results/{date}
  once finals are in. All state in localStorage.
*/

export default function PicksBoard({ pool, today }: { pool: Pick[]; today: string }) {
  const [store, setStore] = useState<PicksStore>({});
  const [hydrated, setHydrated] = useState(false);
  const [search, setSearch] = useState("");
  const now = Date.now();

  // hydrate + grade any past ungraded days
  useEffect(() => {
    const s = loadStore();
    setStore(s);
    setHydrated(true);

    (async () => {
      const pending = Object.values(s).filter(
        (d) => d.graded === null && d.picks.length > 0 && d.date < today,
      );
      if (pending.length === 0) return;
      let changed = false;
      for (const d of pending) {
        const res = await getResults(d.date);
        if (res.ok && res.data.final) {
          s[d.date] = { ...d, graded: scoreDay(d.picks, res.data.hr_by_batter_id) };
          changed = true;
        }
      }
      if (changed) {
        saveStore(s);
        setStore({ ...s });
      }
    })();
  }, [today]);

  const todaySlate = store[today];
  const selected = useMemo<Pick[]>(() => todaySlate?.picks ?? [], [todaySlate]);
  const locked = isSlateLocked(todaySlate, now);
  const stats = useMemo(() => computeStats(store), [store]);

  const selectedIds = new Set(selected.map((p) => p.batterId));

  function persist(picks: Pick[], lockedAt: string | null) {
    const next: PicksStore = {
      ...store,
      [today]: { date: today, picks, lockedAt, graded: todaySlate?.graded ?? null },
    };
    setStore(next);
    saveStore(next);
  }

  function toggle(p: Pick) {
    if (locked) return;
    if (selectedIds.has(p.batterId)) {
      persist(selected.filter((x) => x.batterId !== p.batterId), todaySlate?.lockedAt ?? null);
    } else if (selected.length < MAX_PICKS) {
      persist([...selected, p], todaySlate?.lockedAt ?? null);
    }
  }

  function lockIn() {
    if (selected.length === 0) return;
    persist(selected, new Date().toISOString());
  }

  const filtered = pool.filter((p) => p.name.toLowerCase().includes(search.toLowerCase()));

  // slate math
  const ps = selected.map((p) => p.pGameHr);
  const dist = poissonBinomial(ps);
  const exp = expectedHits(ps);
  const pAtLeast1 = selected.length ? 1 - dist[0] : 0;
  const pAll = selected.length ? dist[selected.length] : 0;

  const history = Object.values(store)
    .filter((d) => d.graded !== null)
    .sort((a, b) => (a.date < b.date ? 1 : -1));

  if (!hydrated) {
    return <div className="py-16 text-center font-mono text-sm text-text-muted">Loading your slate…</div>;
  }

  return (
    <div className="space-y-5">
      {/* stats */}
      <div className="grid grid-cols-3 gap-2">
        <Stat label="Points" value={String(stats.totalPoints)} accent />
        <Stat label="Streak" value={`${stats.streak}🔥`} />
        <Stat label="Days" value={String(stats.daysPlayed)} />
      </div>

      {/* your slate */}
      <section>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="font-sans font-semibold text-text-pri">
            Your slate <span className="font-mono text-text-muted">{selected.length}/{MAX_PICKS}</span>
          </h2>
          {locked ? (
            <span className="font-mono text-[11px] uppercase tracking-wide text-neon-amber">Locked</span>
          ) : (
            <button
              type="button"
              onClick={lockIn}
              disabled={selected.length === 0}
              className="rounded-md border border-neon-lime/50 bg-neon-lime/10 px-3 py-1 font-mono text-xs font-bold text-neon-lime disabled:opacity-40"
            >
              Lock in
            </button>
          )}
        </div>

        {selected.length === 0 ? (
          <p className="rounded-lg border border-dashed border-white/10 bg-card/50 p-4 text-center font-sans text-sm text-text-muted">
            Pick up to {MAX_PICKS} hitters below to homer tonight.
          </p>
        ) : (
          <>
            <div className="space-y-1.5">
              {selected.map((p) => {
                return (
                  <div key={p.batterId} className="flex items-center gap-2 rounded-lg border border-white/5 bg-card px-2.5 py-2">
                    <Headshot batterId={p.batterId} name={p.name} size={32} />
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-sans text-sm text-text-pri">{p.name}</div>
                      <div className="truncate font-mono text-[10px] text-text-muted">vs {p.oppPitcher}</div>
                    </div>
                    <span className="font-mono text-xs text-neon-lime">{formatPct(p.pGameHr)}</span>
                    {!locked && (
                      <button
                        type="button"
                        onClick={() => toggle(p)}
                        aria-label={`Remove ${p.name}`}
                        className="ml-1 rounded px-1.5 font-mono text-text-muted hover:text-neon-hot"
                      >
                        ✕
                      </button>
                    )}
                  </div>
                );
              })}
            </div>

            {/* slate odds */}
            <div className="mt-3 rounded-lg border border-white/5 bg-card p-3">
              <div className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-xs">
                <span className="text-text-muted">≥1 homers <span className="text-text-pri">{formatPct(pAtLeast1)}</span></span>
                <span className="text-text-muted">all {selected.length} homer <span className="text-text-pri">{formatPct(pAll)}</span></span>
                <span className="text-text-muted">expected <span className="text-text-pri">{exp.toFixed(2)}</span></span>
              </div>
              <div className="mt-2 flex gap-1">
                {dist.map((d, k) => (
                  <div key={k} className="flex-1 text-center">
                    <div className="mx-auto w-full rounded-t bg-neon-cyan/70" style={{ height: `${Math.max(2, d * 60)}px` }} />
                    <div className="mt-1 font-mono text-[9px] text-text-muted">{k}</div>
                  </div>
                ))}
              </div>
              <div className="mt-1 text-center font-mono text-[10px] text-text-muted">how many of your picks homer</div>
            </div>
          </>
        )}
      </section>

      {/* pool */}
      {!locked && (
        <section>
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search hitters…"
            className="mb-2 w-full rounded-lg border border-white/10 bg-card px-3 py-2 font-sans text-sm text-text-pri placeholder:text-text-muted focus:border-neon-lime/50 focus:outline-none"
          />
          <div className="max-h-[28rem] space-y-1 overflow-y-auto pr-1">
            {filtered.map((p) => {
              const on = selectedIds.has(p.batterId);
              const full = !on && selected.length >= MAX_PICKS;
              return (
                <button
                  key={p.batterId}
                  type="button"
                  onClick={() => toggle(p)}
                  disabled={full}
                  className={`flex w-full items-center gap-2 rounded-lg border px-2.5 py-1.5 text-left transition-colors ${
                    on ? "border-neon-lime/50 bg-neon-lime/10" : "border-white/5 bg-card hover:bg-hover"
                  } ${full ? "opacity-40" : ""}`}
                >
                  <Headshot batterId={p.batterId} name={p.name} size={28} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-sans text-sm text-text-pri">{p.name}</div>
                    <div className="truncate font-mono text-[10px] text-text-muted">vs {p.oppPitcher}</div>
                  </div>
                  <span className="font-mono text-xs text-text-muted">{formatPct(p.pGameHr)}</span>
                  <span className={`w-5 text-center font-mono ${on ? "text-neon-lime" : "text-text-muted"}`}>
                    {on ? "✓" : "+"}
                  </span>
                </button>
              );
            })}
          </div>
        </section>
      )}

      {/* history */}
      {history.length > 0 && (
        <section>
          <h2 className="mb-2 font-sans font-semibold text-text-pri">History</h2>
          <div className="space-y-1.5">
            {history.map((d: DaySlate) => (
              <div key={d.date} className="flex items-center justify-between rounded-lg border border-white/5 bg-card px-3 py-2">
                <span className="font-mono text-xs text-text-muted">{d.date}</span>
                <span className="font-mono text-xs text-text-pri">
                  {d.graded!.correct}/{d.graded!.total} correct
                  {d.graded!.perfect && <span className="ml-1 text-neon-lime">PERFECT</span>}
                </span>
                <span className="font-mono text-sm font-bold text-neon-lime">+{d.graded!.points}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      <p className="pt-2 text-center font-mono text-[10px] text-text-muted">
        Free to play · points only · picks saved on this device
      </p>
    </div>
  );
}

function Stat({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="rounded-lg border border-white/5 bg-card px-2 py-3 text-center">
      <div className={`font-mono text-xl font-bold ${accent ? "text-neon-lime" : "text-text-pri"}`}>{value}</div>
      <div className="mt-0.5 font-mono text-[10px] uppercase text-text-muted">{label}</div>
    </div>
  );
}
