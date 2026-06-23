# Operation Exit

Operation Exit is a frontend-only React/Vite demo for an 8-week career-transition course. It is designed as a mission-control dashboard that helps an AuDHD professional move from job-search avoidance into daily execution.

## Product structure

- `src/data/courseContent.js` is the content seam. Replace mock arrays/objects here when the real course content is ready.
- `src/components/Primitives.jsx` contains shared layout and UI primitives.
- `src/pages/` contains route-level product surfaces: Home, Dashboard, Roadmap, Today’s Rep, Tracker, Outreach, Interview Gym, Portfolio, and Review.
- `src/hooks/useRepCompletion.js` contains the first local interaction seam for rep completion state.
- `vite.config.js` enables the official React plugin so Vite compiles React JSX reliably.

## Run locally

```bash
npm install
npm run dev
```

Open the local URL Vite prints, usually `http://localhost:5173/`. If the page is blank, open the browser developer console first; a blank Vite page usually means the JavaScript app crashed before React rendered.

## Build

```bash
npm run build
```

## Deploy to Vercel

The included `vercel.json` builds with Vite, serves `dist`, and rewrites SPA routes back to `index.html`.
