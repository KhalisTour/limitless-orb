// Zod schemas derived from the existing mock shapes in src/data/*.
// These ARE the contract: every endpoint's structured output must match the
// corresponding mock's return object so the React components render unchanged.
//
// STRICT-MODE RULE (OpenAI Structured Outputs): every field must be present and
// additionalProperties is false. Any optional field MUST be declared .nullable()
// (NOT .optional()), or the strict schema is rejected.
import { z } from 'zod';

// --- jobs/score (flat object, read by RoleMatchScorer) ---
export const scoreSchema = z.object({
  matchScore: z.number(),
  skillMatch: z.number(),
  proofMatch: z.number(),
  logisticsFit: z.number(),
  strategicValue: z.number(),
  applicationDifficulty: z.string(),
  keywords: z.array(z.string()),
  recommendedProofAssets: z.array(z.string()),
  recommendedResumeVersion: z.string(),
  nextAction: z.string(),
  suggestedRep: z.string(),
  reason: z.string(),
});

// --- outreach/generate ---
export const outreachSchema = z.object({
  type: z.string(),
  subject: z.string(),
  message: z.string(),
  followUp: z.string(),
  wordCount: z.number(),
});

// --- interview/generate ---
export const interviewSchema = z.object({
  point: z.string(),
  evidence: z.string(),
  judgment: z.string(),
  fullAnswer: z.string(),
});

// --- recovery/generate ---
export const recoverySchema = z.object({
  title: z.string(),
  why: z.string(),
  steps: z.array(z.string()),
  timeEstimate: z.string(),
  xp: z.number(),
  completionCriteria: z.string(),
});

// --- coach (matches generateCoachAction; mode-specific keys are nullable) ---
export const coachSchema = z.object({
  answer: z.string(),
  reason: z.string(),
  nextRep: z.string(),
  timeEstimate: z.string(),
  xp: z.number(),
  suggestedAction: z.string(),
  relatedPage: z.string(),
  // optional mode-specific keys -> nullable for strict mode
  strongerVersion: z.string().nullable(),
  keywords: z.array(z.string()).nullable(),
  proofAngle: z.string().nullable(),
  score: scoreSchema.nullable(),
  outreach: outreachSchema.nullable(),
  interview: interviewSchema.nullable(),
  recovery: recoverySchema.nullable(),
  bottleneck: z.string().nullable(),
  stale: z.array(z.string()).nullable(),
  nextWeekFocus: z.string().nullable(),
});

// --- resume/analyze ---
// tailoredSummaries MUST be an object keyed by lane (ResumeLab does
// Object.entries on it). Strict mode forbids arbitrary keys, so the lanes are
// fixed: Marketing Analyst, Product Ops, CRM/Lifecycle, Business Analyst, Growth Analyst.
const tailoredSummariesSchema = z.object({
  marketingAnalyst: z.string(),
  productOps: z.string(),
  crmLifecycle: z.string(),
  businessAnalyst: z.string(),
  growthAnalyst: z.string(),
});

const roleMatchSchema = z.object({
  matchScore: z.number(),
  skillMatch: z.number(),
  proofMatch: z.number(),
  logisticsFit: z.number(),
  strategicValue: z.number(),
  applicationDifficulty: z.string(),
  reason: z.string(),
  nextAction: z.string(),
});

export const resumeAnalysisSchema = z.object({
  id: z.string(),
  resumeVersion: z.string(),
  targetLane: z.string(),
  strengths: z.array(z.string()),
  quantifiedAchievements: z.array(
    z.object({ original: z.string(), improved: z.string(), metric: z.string() })
  ),
  weakBullets: z.array(
    z.object({ bullet: z.string(), issue: z.string(), fix: z.string() })
  ),
  tailoredSummaries: tailoredSummariesSchema,
  bulletSwaps: z.array(
    z.object({ targetLane: z.string(), before: z.string(), after: z.string(), reason: z.string() })
  ),
  roleMatch: roleMatchSchema,
});

// --- jobs/tailor ---
export const tailorSchema = z.object({
  selectedJob: z.string(),
  selectedResumeVersion: z.string(),
  jobKeywords: z.array(z.string()),
  missingKeywords: z.array(z.string()),
  suggestedHeadline: z.string(),
  suggestedSummary: z.string(),
  bulletChanges: z.array(
    z.object({ before: z.string(), after: z.string(), why: z.string() })
  ),
  recommendedProofCard: z.string(),
  applicationNotes: z.array(z.string()),
});
