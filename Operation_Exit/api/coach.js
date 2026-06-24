import { withApi } from '../server/lib/withApi.js';
import { structured, hasOpenAI, model } from '../server/lib/openaiClient.js';
import { coachSchema } from '../server/lib/schemas.js';
import { systemFor, userPayload } from '../server/prompts/operationExitPrompts.js';
import { validateCoach } from '../server/lib/validate.js';
import { generateCoachAction } from '../src/data/coachActions.js';

export default withApi({ name: 'coach', validate: validateCoach }, async (body) => {
  const { mode = 'What is my next rep?', context = '', currentPage = null, stateSnapshot = null } = body;
  if (hasOpenAI()) {
    try {
      const result = await structured(
        model(),
        systemFor('coach'),
        userPayload({ mode, context, currentPage, stateSnapshot }),
        coachSchema,
        'coach_action'
      );
      return { ...result, fallback: false };
    } catch {
      // fall through to local mock
    }
  }
  return { ...generateCoachAction(mode, context), fallback: true };
});
