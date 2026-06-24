import { withApi } from '../../server/lib/withApi.js';
import { structured, hasOpenAI, model } from '../../server/lib/openaiClient.js';
import { interviewSchema } from '../../server/lib/schemas.js';
import { systemFor, userPayload } from '../../server/prompts/operationExitPrompts.js';
import { validateInterview } from '../../server/lib/validate.js';
import { generateInterviewAnswer } from '../../src/data/interviewContent.js';

export default withApi({ name: 'interview/generate', validate: validateInterview }, async (body) => {
  const { question, proofProject = {}, targetLane = 'Growth Analyst' } = body;
  if (hasOpenAI()) {
    try {
      const result = await structured(
        model(),
        systemFor('interview'),
        userPayload({ question, proofProject, targetLane }),
        interviewSchema,
        'interview_answer'
      );
      return { ...result, fallback: false };
    } catch {
      // fall through
    }
  }
  return { ...generateInterviewAnswer(question, proofProject, targetLane), fallback: true };
});
