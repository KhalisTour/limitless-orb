"use client";

import { useState } from "react";
import type { MatchupZoneCell, ZoneCell } from "@/lib/types";
import ZoneGrid, { type ZoneValueKey, type CellLike } from "./ZoneGrid";

/*
  MatchupZones (PART 3). Desktop >=640px: three independent ZoneGrids
  [PITCHER | BATTER | DANGER]. Mobile <640px: single grid + pill tabs
  [BATTER | PITCHER | DANGER], DANGER default. Only DANGER pulses.
  Do NOT three-layer translucent SVGs.
*/

type Tab = "BATTER" | "PITCHER" | "DANGER";

interface Props {
  matchupZones: Record<string, MatchupZoneCell>;
  batterZones: Record<string, ZoneCell>;
  baseline: Record<string, ZoneCell>;
  hottestZone: string | null;
  hasMatchup: boolean;
  playerFound: boolean;
}

function gridFor(
  tab: Tab,
  p: Props,
): { zones: Record<string, CellLike>; valueKey: ZoneValueKey; hottest: string | null; showCounts: boolean; desatAll: boolean; baseline?: Record<string, ZoneCell> } {
  if (tab === "BATTER") {
    return {
      zones: p.playerFound ? p.batterZones : {},
      valueKey: "hr_pct",
      hottest: null,
      showCounts: true,
      desatAll: !p.playerFound,
      baseline: p.baseline,
    };
  }
  if (tab === "PITCHER") {
    return {
      zones: p.matchupZones,
      valueKey: "pitcher_tendency",
      hottest: null,
      showCounts: false,
      desatAll: false,
    };
  }
  return {
    zones: p.matchupZones,
    valueKey: "danger_pct",
    hottest: p.hottestZone,
    showCounts: false,
    desatAll: false,
  };
}

const TAB_CAPTION: Record<Tab, string> = {
  BATTER: "Where this hitter does damage",
  PITCHER: "Where this pitcher locates",
  DANGER: "Where the homer is most likely",
};

function GridPanel({ tab, props, size }: { tab: Tab; props: Props; size: number }) {
  const g = gridFor(tab, props);
  return (
    <ZoneGrid
      zones={g.zones}
      valueKey={g.valueKey}
      hottestZone={g.hottest}
      showCounts={g.showCounts}
      desaturateAll={g.desatAll}
      baseline={g.baseline}
      size={size}
    />
  );
}

export default function MatchupZones(props: Props) {
  const [tab, setTab] = useState<Tab>("DANGER");

  // Tabs available depend on whether matchup data exists (error #8).
  const tabs: Tab[] = props.hasMatchup ? ["BATTER", "PITCHER", "DANGER"] : ["BATTER"];
  const activeTab = props.hasMatchup ? tab : "BATTER";

  return (
    <div>
      {/* Mobile: single grid + tabs */}
      <div className="sm:hidden">
        {props.hasMatchup && (
          <div className="mb-3 flex gap-1.5">
            {tabs.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setTab(t)}
                className={`flex-1 rounded-full border px-3 py-1.5 font-mono text-xs transition-colors ${
                  activeTab === t
                    ? "border-neon-lime/60 bg-neon-lime/10 text-neon-lime"
                    : "border-white/10 text-text-muted"
                }`}
              >
                {t}
              </button>
            ))}
          </div>
        )}
        <div className="flex flex-col items-center">
          <GridPanel tab={activeTab} props={props} size={320} />
          <p className="mt-2 font-sans text-xs text-text-muted">{TAB_CAPTION[activeTab]}</p>
        </div>
      </div>

      {/* Desktop: three side-by-side */}
      <div className="hidden sm:flex sm:items-start sm:justify-center sm:gap-4">
        {(props.hasMatchup ? (["PITCHER", "BATTER", "DANGER"] as Tab[]) : (["BATTER"] as Tab[])).map(
          (t) => (
            <div key={t} className="flex flex-col items-center">
              <span className="mb-1 font-mono text-xs uppercase tracking-wide text-text-muted">{t}</span>
              <GridPanel tab={t} props={props} size={240} />
              <p className="mt-1.5 max-w-[240px] text-center font-sans text-[11px] text-text-muted">
                {TAB_CAPTION[t]}
              </p>
            </div>
          ),
        )}
      </div>
    </div>
  );
}
