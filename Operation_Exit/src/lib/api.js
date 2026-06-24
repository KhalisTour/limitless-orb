// Frontend API client. Always calls relative /api/* URLs: in dev these hit the
// Vite proxy -> Express; in prod they hit Vercel functions same-origin. No
// environment-specific base URL ever lives in client code.
//
// Fallback logic does NOT live here — call sites catch and use the local mock
// import (via useAsyncAction) so the existing mock functions remain the
// fallback path.

const TOKEN = import.meta.env.VITE_OPERATION_EXIT_OWNER_TOKEN;
const TIMEOUT_MS = 30_000;

async function post(path, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        // Attach the owner token only if it is configured.
        ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}),
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`api_${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

export const apiGenerateCoachAction = (mode, context, extra = {}) =>
  post('/api/coach', { mode, context, ...extra });

export const apiAnalyzeResume = (input) => post('/api/resume/analyze', input);

export const apiScoreJob = (input) => post('/api/jobs/score', input);

export const apiTailorResumeToJob = (resumeProfile, jobInput, proofLibrary) =>
  post('/api/jobs/tailor', { resumeProfile, jobInput, proofLibrary });

export const apiGenerateOutreachMessage = (input) => post('/api/outreach/generate', input);

export const apiGenerateInterviewAnswer = (input) => post('/api/interview/generate', input);

export const apiGenerateRecoveryRep = (input) => post('/api/recovery/generate', input);

export const apiLoadState = (userId = 'owner') => post('/api/state/load', { userId });

export const apiSaveState = (patch, userId = 'owner') => post('/api/state/save', { userId, patch });

// Mirror a saved item into localStorage so it still appears when server
// persistence is a no-op (e.g. Vercel without a database).
function mirrorLocal(collection, item) {
  try {
    const key = `operation-exit-${collection}`;
    const arr = JSON.parse(window.localStorage.getItem(key) || '[]');
    arr.push(item);
    window.localStorage.setItem(key, JSON.stringify(arr));
  } catch {
    /* localStorage unavailable — ignore */
  }
}

// Append an item to a server-side collection (savedJobs / savedOutreach /
// savedAnswers). Loads current state, appends, saves, and always mirrors to
// localStorage. Returns { persisted } so callers can show "Saved" vs
// "Saved locally".
export async function saveToCollection(collection, item, userId = 'owner') {
  mirrorLocal(collection, item);
  try {
    const current = await apiLoadState(userId);
    const existing = Array.isArray(current?.state?.[collection]) ? current.state[collection] : [];
    const result = await apiSaveState({ [collection]: [...existing, item] }, userId);
    return { persisted: result?.persisted !== false };
  } catch {
    return { persisted: false };
  }
}
