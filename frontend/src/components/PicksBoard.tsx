"use client";

import { useEffect, useMemo, useState } from "react";
import { getResults } from "@/lib/api";
import { useSlate } from "@/lib/slate";
import { formatPct } from "@/lib/format";
import {
  MAX_PICKS,
  computeStats,
  expectedHits,
  isSlateLocked,
  loadStore,
  poissonBinomial,
  poolToPick,
  saveStore,
  scoreDay,
  type DaySlate,
  type GameGroup,
  type PicksStore,
  type PoolHitter,
} from "@/lib/picks";
import Headshot from "./Headshot";
import LabelBadge from "./LabelBadge";

/*
  Daily HR Pick'em (points + streaks, anonymous/on-device).
  Selection is organized as a horizontal game nav: each game is a collapsible
  column ([AWAY @ HOME  time ET]); tapping it reveals that game's hitters,
  sorted Elite -> Low, as minimal cards (photo + name + label + select bubble).
*/

export default function PicksBoard({ games, today }: { games: GameGroup[]; today: string }) {
  const [store, setStore] = useState<PicksStore>({});
  const [hydrated, setHydrated] = useState(false);
  const [openGame, setOpenGame] = useState<number | null>(null);
  const slate = useSlate();
  const now = Date.now();

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
  const selected = useMemo(() => todaySlate?.picks ?? [], [todaySlate]);
  const locked = isSlateLocked(todaySlate, now);
  const stats = useMemo(() => computeStats(store), [store]);
  const selectedIds = new Set(selected.map((p) => p.batterId));

  function persist(picks: typeof selected, lockedAt: string | null) {
    const next: PicksStore = {
      ...store,
      [today]: { date: today, picks, lockedAt, graded: todaySlate?.graded ?? null },
    };
    setStore(next);
    saveStore(next);
  }

  function toggle(h: PoolHitter) {
    if (locked) return;
    if (selectedIds.has(h.batterId)) {
      persist(selected.filter((x) => x.batterId !== h.batterId), todaySlate?.lockedAt ?? null);
    } else if (selected.length < MAX_PICKS) {
      persist([...selected, poolToPick(h)], todaySlate?.lockedAt ?? null);
    }
  }

  function lockIn() {
    if (selected.length === 0) return;
    persist(selected, new Date().toISOString());
  }

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

  const activeGame = games.find((g) => g.gameId === openGame) ?? null;

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
            Tap a game below and pick up to {MAX_PICKS} hitters to homer tonight.
          </p>
        ) : (
          <>
            <div className="space-y-1.5">
              {selected.map((p) => (
                <div key={p.batterId} className="flex items-center gap-2 rounded-lg border border-white/5 bg-card px-2.5 py-2">
                  <Headshot batterId={p.batterId} name={p.name} size={32} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-sans text-sm text-text-pri">{p.name}</div>
                    <div className="truncate font-mono text-[10px] text-text-muted">vs {p.oppPitcher}</div>
                  </div>
                  <LabelBadge label={slate.labelFor(p.pGameHr)} size="sm" />
                  {!locked && (
                    <button
                      type="button"
                      onClick={() =>
                        persist(selected.filter((x) => x.batterId !== p.batterId), todaySlate?.lockedAt ?? null)
                      }
                      aria-label={`Remove ${p.name}`}
                      className="ml-1 rounded px-1.5 font-mono text-text-muted hover:text-neon-hot"
                    >
                      ✕
                    </button>
                  )}
                </div>
              ))}
            </div>

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

      {/* game nav + selection (hidden once the slate is locked) */}
      {!locked && (
        <section>
          <h2 className="mb-2 font-sans font-semibold text-text-pri">Tonight&apos;s games</h2>
          {games.length === 0 ? (
            <p className="rounded-lg border border-dashed border-white/10 bg-card/50 p-4 text-center font-sans text-sm text-text-muted">
              No games with posted lineups yet.
            </p>
          ) : (
            <>
              {/* horizontal game nav — each game is a collapsible column */}
              <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-2">
                {games.map((g) => {
                  const active = g.gameId === openGame;
                  const inThis = selected.filter((p) => p.gameId === g.gameId).length;
                  return (
                    <button
                      key={g.gameId}
                      type="button"
                      data-testid="game-chip"
                      onClick={() => setOpenGame(active ? null : g.gameId)}
                      className={`shrink-0 rounded-lg border px-3 py-2 text-left transition-colors ${
                        active
                          ? "border-neon-lime/60 bg-neon-lime/10"
                          : "border-white/10 bg-card hover:bg-hover"
                      }`}
                    >
                      <div className={`font-mono text-sm font-bold ${active ? "text-neon-lime" : "text-text-pri"}`}>
                        {g.label}
                        {inThis > 0 && <span className="ml-1 text-neon-lime">·{inThis}</span>}
                      </div>
                      <div className="font-mono text-[10px] text-text-muted">{g.timeET ?? "—"}</div>
                    </button>
                  );
                })}
              </div>

              {/* opened game's hitters, Elite -> Low */}
              {activeGame && (
                <div className="mt-1 space-y-1.5">
                  {activeGame.hitters.map((h) => {
                    const on = selectedIds.has(h.batterId);
                    const full = !on && selected.length >= MAX_PICKS;
                    return (
                      <button
                        key={h.batterId}
                        type="button"
                        data-testid="pick-card"
                        onClick={() => toggle(h)}
                        disabled={full}
                        className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors ${
                          on ? "border-neon-lime/50 bg-neon-lime/10" : "border-white/5 bg-card hover:bg-hover"
                        } ${full ? "opacity-40" : ""}`}
                      >
                        <Headshot batterId={h.batterId} name={h.name} size={40} />
                        <span className="min-w-0 flex-1 truncate font-sans text-sm text-text-pri">{h.name}</span>
                        <LabelBadge label={slate.labelFor(h.pPerPa)} size="sm" />
                        <span
                          aria-hidden
                          className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 transition-colors ${
                            on ? "border-neon-lime bg-neon-lime text-base" : "border-text-muted bg-transparent"
                          }`}
                        >
                          {on && <span className="font-mono text-xs font-bold">✓</span>}
                        </span>
                      </button>
                    );
                  })}
                </div>
              )}
            </>
          )}
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
