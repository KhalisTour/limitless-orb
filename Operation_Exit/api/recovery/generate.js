import { withApi } from '../../server/lib/withApi.js';
import { structured, hasOpenAI, modelCheap } from '../../server/lib/openaiClient.js';
import { recoverySchema } from '../../server/lib/schemas.js';
import { systemFor, userPayload } from '../../server/prompts/operationExitPrompts.js';
import { validateRecovery } from '../../server/lib/validate.js';
import { generateRecoveryRep } from '../../src/data/coachActions.js';

export default withApi({ name: 'recovery/generate', validate: validateRecovery }, async (body) => {
  if (hasOpenAI()) {
    try {
      const result = await structured(
        modelCheap(),
        systemFor('recovery'),
        userPayload(body),
        recoverySchema,
        'recovery_rep'
      );
      return { ...result, fallback: false };
    } catch {
      // fall through
    }
  }
  return { ...generateRecoveryRep(), fallback: true };
});
