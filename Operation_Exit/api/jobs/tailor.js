import { withApi } from '../../server/lib/withApi.js';
import { structured, hasOpenAI, model } from '../../server/lib/openaiClient.js';
import { tailorSchema } from '../../server/lib/schemas.js';
import { systemFor, userPayload } from '../../server/prompts/operationExitPrompts.js';
import { validateTailor } from '../../server/lib/validate.js';
import { tailorResumeToJob } from '../../src/data/jobCaptureData.js';

export default withApi({ name: 'jobs/tailor', validate: validateTailor }, async (body) => {
  const { resumeProfile = {}, jobInput = {}, proofLibrary = [] } = body;
  if (hasOpenAI()) {
    try {
      const result = await structured(
        model(),
        systemFor('tailor'),
        userPayload({ resumeProfile, jobInput, proofLibrary }),
        tailorSchema,
        'resume_tailor'
      );
      return { ...result, fallback: false };
    } catch {
      // fall through
    }
  }
  // Mock already supplies sensible defaults when either side is empty.
  return { ...tailorResumeToJob(resumeProfile, jobInput), fallback: true };
});
