# Orb AI — Frontend (v1)

Web-first (Next.js 16 / App Router / Tailwind v4) home-run scouting overlay.
"Video-game scouting overlay" aesthetic: dark near-black surfaces, mono
numerals, a single neon-chartreuse (`#c8ff00`) accent, strike-zone heat maps,
trajectory arcs, and a Monte-Carlo team HR simulation.

## Run it

```bash
npm install
npm run dev          # http://localhost:3000
```

### Mock vs live data

The entire UI is renderable before the backend is wired:

- **`NEXT_PUBLIC_API_URL` unset** → mock mode (`src/lib/mock.ts`). Covers every
  contract edge case: full games, one-side-errored, both-sides-errored, a
  rookie with `batter_id: null`, a `stats: null` hitter, a partial-stats
  hitter, a `game_datetime: null` game, and a `stale: true` response.
- **`NEXT_PUBLIC_API_URL` set** → live API. Local backend:
  `NEXT_PUBLIC_API_URL=http://localhost:8000`.

See `.env.example`.

## Architecture (PART 5 contract)

State surface is **Next.js context + SWR only** — no Redux/Zustand.

```
src/lib/
  types.ts        verified API contract types (PART 1)
  api.ts          8 typed fetch wrappers -> discriminated ApiResult<T>
  mock.ts         realistic mock data for every endpoint shape
  colors.ts       zoneColor() — the single shared cold->hot scale
  percentile.ts   slate-relative probability + stat percentile compute
  slate.tsx       React context holding ELITE/HIGH/MED/LOW + stat percentiles
  format.ts       formatGameTime / formatPct / formatStat
  trajectory.ts   pure arc visibility helpers (server + client safe)
src/components/   the ONLY components (PART 5) — ProbabilityRing, HitterCard,
                  ZoneGrid, TrajectoryArc, MatchupZones, MonteCarloBar,
                  StatGrid, EmptySideCard, PendingLineupHero, OfflineShell,
                  StaleBanner, DatePicker, Logo, GameCard, GameView,
                  MatchupHero, ModelViews, ParkBadge, LabelBadge,
                  PitchFamilyToggle, Nav
src/app/          / (games)  ·  /game/[id]  ·  /top-picks
                  /matchup/[game_id]/[batter_id]/[pitcher_name]  ·  /accuracy
```

### Design tokens

Tailwind v4 is CSS-first: tokens live in `@theme` inside
`src/app/globals.css` (there is no `tailwind.config.ts` in v4). Animations are
GPU-composited CSS only — ring fill, zone pulse, zone color swap, arc reveal.
Framer Motion is intentionally **not** used (none of the v1 verify items need
it; it stays out to keep the matchup page at 60fps).

## Deploy (Vercel)

1. Import the repo, set the project root to `frontend/`.
2. Set `NEXT_PUBLIC_API_URL` to the Railway backend URL.
3. Deploy. `vercel.json` pins the Next.js framework preset.

Revalidation is per-route via `export const revalidate` (300s for the slate,
game, top-picks and matchup pages; 3600s for accuracy).

### CORS

Backend `allow_origin_regex` is
`https?://(localhost(:\d+)?|.*\.vercel\.app)` — Vercel preview deployments
match automatically. **Production custom domains do NOT** — ping the backend
owner to extend the regex when wiring a custom domain.

## v1 verify checklist status

Done and verified against mock data (`npm run build` + runtime smoke test):

- Zero TypeScript errors; Page 1 renders with mock data and with a live
  `NEXT_PUBLIC_API_URL`.
- ProbabilityRing animates on scroll (IntersectionObserver), all three sizes
  (40/60/100).
- ZoneGrid renders all 13 zones with CORNER coords for 11–14; string-key dev
  assert in place.
- EmptySideCard / PendingLineupHero / "Lineup TBD" / `batter_id:null` disabled
  CTA / `stats:null` hidden grid / `game_datetime:null` omitted time / stale
  banner — all handled.
- DatePicker bound to `/api/dates` with disabled arrows at the ends.
- Matchup: mobile single-grid tabs + desktop three side-by-side grids;
  pitcher names URL-encoded (`José`, `Yusei Kikuchi`).
- Trajectory hide/caption rules; CSS `@keyframes` hot-zone pulse;
  MonteCarloBar bucket sort; slate-relative labels and stat dots.

Pending (requires accounts / a phone, not doable headless here):

- `vercel deploy` and on-device 60fps / 4-tap cold-open check.

## v1 defers (do not build here)

Prop-bet edge/parlay layer, real pitch-family toggle (rendered disabled),
player headshots (gradient placeholder), auto-refresh polling, calibration
scatterplot, PWA manifest, native-only haptics/push.
