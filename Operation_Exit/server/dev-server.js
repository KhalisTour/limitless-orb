// Tiny Express server for local dev. `vite dev` does not execute the /api
// functions, so this imports each Vercel-style handler and mounts it at its
// matching path. The handlers are written once (Vercel signature) and reused
// here — no logic is duplicated.
import express from 'express';

import health from '../api/health.js';
import coach from '../api/coach.js';
import resumeAnalyze from '../api/resume/analyze.js';
import jobsScore from '../api/jobs/score.js';
import jobsTailor from '../api/jobs/tailor.js';
import outreachGenerate from '../api/outreach/generate.js';
import interviewGenerate from '../api/interview/generate.js';
import recoveryGenerate from '../api/recovery/generate.js';
import stateLoad from '../api/state/load.js';
import stateSave from '../api/state/save.js';

const app = express();
app.use(express.json({ limit: '2mb' }));

app.all('/api/health', (req, res) => health(req, res));
app.all('/api/coach', (req, res) => coach(req, res));
app.all('/api/resume/analyze', (req, res) => resumeAnalyze(req, res));
app.all('/api/jobs/score', (req, res) => jobsScore(req, res));
app.all('/api/jobs/tailor', (req, res) => jobsTailor(req, res));
app.all('/api/outreach/generate', (req, res) => outreachGenerate(req, res));
app.all('/api/interview/generate', (req, res) => interviewGenerate(req, res));
app.all('/api/recovery/generate', (req, res) => recoveryGenerate(req, res));
app.all('/api/state/load', (req, res) => stateLoad(req, res));
app.all('/api/state/save', (req, res) => stateSave(req, res));

app.listen(3001, () => console.log('Operation Exit dev API on :3001'));
