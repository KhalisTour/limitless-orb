import { withApi } from '../../server/lib/withApi.js';
import { structured, hasOpenAI, model } from '../../server/lib/openaiClient.js';
import { scoreSchema } from '../../server/lib/schemas.js';
import { systemFor, userPayload } from '../../server/prompts/operationExitPrompts.js';
import { validateScore } from '../../server/lib/validate.js';
import { scoreJob } from '../../src/data/jobCaptureData.js';

export default withApi({ name: 'jobs/score', validate: validateScore }, async (body) => {
  if (hasOpenAI()) {
    try {
      const result = await structured(
        model(),
        systemFor('jobScore'),
        userPayload(body),
        scoreSchema,
        'job_score'
      );
      return { ...result, fallback: false };
    } catch {
      // fall through
    }
  }
  return { ...scoreJob(body), fallback: true };
});
