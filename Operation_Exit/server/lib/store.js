// State-blob persistence. localStorage on the client remains the source of
// truth; this is a single OPTIONAL blob keyed by user id (default "owner").
// There are no normalized tables and no ORM.
//
// Driver selection:
//   - Vercel no-op:    process.env.VERCEL set AND no DATABASE_URL -> never write to disk.
//   - Postgres:        DATABASE_URL set AND the `pg` package is installed (drop-in; see DDL below).
//   - JSON file:       USE_LOCAL_JSON_DB !== "false", no DATABASE_URL, not on Vercel.
//   - In-memory:       last-resort fallback (keeps the API working, no persistence).
//
// Postgres drop-in DDL (run once, then `npm i pg`):
//   create table operation_exit_state (
//     user_id    text primary key,
//     data       jsonb not null,
//     updated_at timestamptz not null default now()
//   );
//   -- upsert: insert ... on conflict (user_id) do update set data=excluded.data, updated_at=now();
//
// If DATABASE_URL points at a Supabase project and pg is not installed, you can
// instead wire a fetch-based Supabase REST upsert against the same table using
// SUPABASE_URL + SUPABASE_SERVICE_KEY. Until one of those is present, this file
// transparently falls back to the JSON-file driver so the app keeps working.
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DB_FILE = path.join(__dirname, '..', 'data', 'operation-exit-db.json');

const onVercel = Boolean(process.env.VERCEL);
const hasDatabaseUrl = Boolean(process.env.DATABASE_URL);
const useLocalJson = (process.env.USE_LOCAL_JSON_DB ?? 'true') !== 'false';

function defaultState(userId = 'owner') {
  const now = new Date().toISOString();
  return {
    id: userId,
    currentWeek: 1,
    weeklyXp: 0,
    streak: 0,
    optionsCreated: 0,
    applicationsCount: 0,
    outreachCount: 0,
    interviewReps: 0,
    followUpsDue: 0,
    lastCompletedRepAt: null,
    createdAt: now,
    updatedAt: now,
    savedJobs: [],
    savedAnswers: [],
    savedOutreach: [],
  };
}

// ---- Derived pipeline counters (computed on load) ----
export function deriveCounters(blob) {
  const savedJobs = Array.isArray(blob.savedJobs) ? blob.savedJobs : [];
  const savedOutreach = Array.isArray(blob.savedOutreach) ? blob.savedOutreach : [];
  const savedAnswers = Array.isArray(blob.savedAnswers) ? blob.savedAnswers : [];

  const applied = savedJobs.filter((j) => /applied|interview|screen|offer|final/i.test(j.status || ''));
  const sevenDaysAgo = Date.now() - 7 * 24 * 60 * 60 * 1000;
  const stale = savedJobs.filter((j) => {
    const t = Date.parse(j.createdAt || '');
    return Number.isFinite(t) && t < sevenDaysAgo;
  });
  const highMatch = savedJobs.filter((j) => Number(j.matchScore) >= 85);
  const best = [...savedJobs].sort((a, b) => (Number(b.matchScore) || 0) - (Number(a.matchScore) || 0))[0];

  return {
    followUpsDue: blob.followUpsDue || 0,
    applicationsThisWeek: applied.length || blob.applicationsCount || 0,
    outreachThisWeek: savedOutreach.length || blob.outreachCount || 0,
    interviewsScheduled: savedAnswers.length || blob.interviewReps || 0,
    bottleneck: savedJobs.length > applied.length ? 'Saved roles not converted' : 'Outreach volume',
    bestNextAction: best ? `${best.role || 'Saved role'}${best.company ? ' · ' + best.company : ''}` : 'Capture one role',
    staleOpportunities: stale.length,
    highMatchSavedRoles: highMatch.length,
  };
}

// ---- Driver detection ----
let pgPool = null;
let pgChecked = false;

async function getPgPool() {
  if (pgChecked) return pgPool;
  pgChecked = true;
  try {
    const { default: pg } = await import('pg');
    pgPool = new pg.Pool({ connectionString: process.env.DATABASE_URL });
    await pgPool.query(
      `create table if not exists operation_exit_state (
         user_id text primary key,
         data jsonb not null,
         updated_at timestamptz not null default now()
       )`
    );
  } catch {
    // pg not installed or connection failed -> fall back to JSON file.
    pgPool = null;
  }
  return pgPool;
}

export async function driverName() {
  if (onVercel && !hasDatabaseUrl) return 'vercel-noop';
  if (hasDatabaseUrl) {
    const pool = await getPgPool();
    if (pool) return 'postgres';
    return 'json-file (postgres pending: install pg)';
  }
  if (useLocalJson && !onVercel) return 'json-file';
  return 'memory';
}

// ---- JSON file helpers ----
async function readJsonFile() {
  try {
    const raw = await fs.readFile(DB_FILE, 'utf8');
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

async function writeJsonFile(all) {
  await fs.mkdir(path.dirname(DB_FILE), { recursive: true });
  await fs.writeFile(DB_FILE, JSON.stringify(all, null, 2), 'utf8');
}

// In-memory last-resort store.
const memory = new Map();

// ---- Public API ----
export async function loadState(userId = 'owner') {
  if (hasDatabaseUrl) {
    const pool = await getPgPool();
    if (pool) {
      const { rows } = await pool.query('select data from operation_exit_state where user_id = $1', [userId]);
      return rows[0]?.data || defaultState(userId);
    }
  }
  if (onVercel) {
    // No server store configured on Vercel -> client localStorage is source of truth.
    return defaultState(userId);
  }
  if (useLocalJson) {
    const all = await readJsonFile();
    return all[userId] || defaultState(userId);
  }
  return memory.get(userId) || defaultState(userId);
}

export async function saveState(userId = 'owner', patch = {}) {
  const current = await loadState(userId);
  const merged = { ...current, ...patch, id: userId, updatedAt: new Date().toISOString() };

  if (hasDatabaseUrl) {
    const pool = await getPgPool();
    if (pool) {
      await pool.query(
        `insert into operation_exit_state (user_id, data, updated_at)
         values ($1, $2, now())
         on conflict (user_id) do update set data = excluded.data, updated_at = now()`,
        [userId, merged]
      );
      return { persisted: true, data: merged };
    }
  }

  if (onVercel) {
    // Vercel no-op: do NOT attempt a filesystem write. The UI treats this as
    // success and keeps using localStorage.
    return {
      persisted: false,
      reason: 'no server store configured; client localStorage is source of truth',
      data: merged,
    };
  }

  if (useLocalJson) {
    const all = await readJsonFile();
    all[userId] = merged;
    await writeJsonFile(all);
    return { persisted: true, data: merged };
  }

  memory.set(userId, merged);
  return { persisted: true, data: merged };
}
