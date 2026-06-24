// Manual integration test (no framework). Hits the local dev server and
// asserts the endpoint contracts. Run with: npm run test:api
// Intended to pass with NO OPENAI_API_KEY (asserts the fallback path).
const BASE = process.env.TEST_BASE || 'http://localhost:3001';

let passed = 0;
let failed = 0;

function assert(cond, label) {
  if (cond) {
    passed += 1;
    console.log(`  ok   ${label}`);
  } else {
    failed += 1;
    console.error(`  FAIL ${label}`);
  }
}

async function get(path) {
  const res = await fetch(`${BASE}${path}`);
  return res.json();
}

async function post(path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

function wordCount(s = '') {
  return s.trim().split(/\s+/).filter(Boolean).length;
}

async function main() {
  console.log(`Operation Exit API test against ${BASE}\n`);

  const health = await get('/api/health');
  assert(health.ok === true, 'GET /api/health -> ok:true');

  const coach = await post('/api/coach', { mode: 'What is my next rep?', context: '' });
  assert(coach.nextRep && coach.timeEstimate && typeof coach.xp === 'number', 'POST /api/coach -> nextRep, timeEstimate, xp');

  const resume = await post('/api/resume/analyze', { resumeText: 'Helped with social media. Worked with CRM.', targetLane: 'CRM / Lifecycle Marketing' });
  assert(Array.isArray(resume.strengths) && resume.strengths.length > 0, 'POST /api/resume/analyze -> non-empty strengths');
  assert(Array.isArray(resume.weakBullets) && resume.weakBullets.length > 0, 'POST /api/resume/analyze -> non-empty weakBullets');
  assert(resume.tailoredSummaries && typeof resume.tailoredSummaries === 'object', 'POST /api/resume/analyze -> tailoredSummaries is object');

  const score = await post('/api/jobs/score', { role: 'CRM Specialist', lane: 'CRM / Lifecycle Marketing', description: 'Lifecycle and conversion CRM role', location: 'Remote' });
  assert(typeof score.matchScore === 'number', 'POST /api/jobs/score -> numeric matchScore');
  assert(Boolean(score.nextAction), 'POST /api/jobs/score -> nextAction present');

  const outreach = await post('/api/outreach/generate', { audience: 'Hiring manager', role: 'Growth Analyst', tone: 'direct' });
  assert(wordCount(outreach.message) < 120, `POST /api/outreach/generate -> message under 120 words (${wordCount(outreach.message)})`);

  const interview = await post('/api/interview/generate', { question: 'Tell me about yourself.', targetLane: 'Growth Analyst' });
  assert(interview.point && interview.evidence && interview.judgment, 'POST /api/interview/generate -> point, evidence, judgment');

  const recovery = await post('/api/recovery/generate', { currentState: {} });
  assert(Boolean(recovery.title), 'POST /api/recovery/generate -> title present');
  assert(Array.isArray(recovery.steps) && recovery.steps.length > 0, 'POST /api/recovery/generate -> steps present');
  const minutes = parseInt(String(recovery.timeEstimate).replace(/\D+/g, ''), 10);
  assert(Number.isFinite(minutes) && minutes < 10, `POST /api/recovery/generate -> timeEstimate under 10 min (${recovery.timeEstimate})`);

  console.log(`\nSummary: ${passed} passed, ${failed} failed`);
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((err) => {
  console.error('Test run failed to reach the dev server:', err.message);
  process.exit(1);
});
