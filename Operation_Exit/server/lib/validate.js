// Input size-limit guards. Each validator returns an error string (sanitized,
// short) when the input is invalid, or null when it passes. withApi turns a
// returned string into a 400 response.

const LIMITS = {
  resumeText: 40_000,
  description: 30_000,
  jobDescription: 30_000,
  outreachContext: 5_000,
  coachContext: 10_000,
};

function tooLong(value, max) {
  return typeof value === 'string' && value.length > max;
}

export function validateCoach(body = {}) {
  if (!body.mode || typeof body.mode !== 'string') return 'mode_required';
  if (tooLong(body.context, LIMITS.coachContext)) return 'context_too_long';
  return null;
}

export function validateResume(body = {}) {
  if (tooLong(body.resumeText, LIMITS.resumeText)) return 'resume_too_long';
  return null;
}

export function validateScore(body = {}) {
  if (tooLong(body.description, LIMITS.description)) return 'description_too_long';
  return null;
}

export function validateTailor(body = {}) {
  const desc = body.jobInput?.description;
  if (tooLong(desc, LIMITS.jobDescription)) return 'description_too_long';
  return null;
}

export function validateOutreach(body = {}) {
  if (tooLong(body.context, LIMITS.outreachContext)) return 'context_too_long';
  return null;
}

export function validateInterview(body = {}) {
  if (!body.question || typeof body.question !== 'string') return 'question_required';
  return null;
}

export function validateRecovery() {
  return null;
}

export function validateState() {
  return null;
}
