import { withApi } from '../../server/lib/withApi.js';
import { structured, hasOpenAI, model } from '../../server/lib/openaiClient.js';
import { resumeAnalysisSchema } from '../../server/lib/schemas.js';
import { systemFor, userPayload } from '../../server/prompts/operationExitPrompts.js';
import { validateResume } from '../../server/lib/validate.js';
import { analyzeResume } from '../../src/data/resumeContent.js';

export default withApi({ name: 'resume/analyze', validate: validateResume }, async (body) => {
  const { resumeText = '', resumeVersion = null, targetLane = 'CRM / Lifecycle Marketing' } = body;
  if (hasOpenAI()) {
    try {
      const result = await structured(
        model(),
        systemFor('resumeAnalyze'),
        userPayload({ resumeText, resumeVersion, targetLane }),
        resumeAnalysisSchema,
        'resume_analysis'
      );
      // Preserve the resumeVersion override the UI relies on.
      return { ...result, resumeVersion: resumeVersion || result.resumeVersion, fallback: false };
    } catch {
      // fall through
    }
  }
  return {
    ...analyzeResume(resumeText, targetLane),
    resumeVersion: resumeVersion || 'Master Resume',
    fallback: true,
  };
});
