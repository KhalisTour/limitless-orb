import { withApi } from '../../server/lib/withApi.js';
import { structured, hasOpenAI, modelCheap } from '../../server/lib/openaiClient.js';
import { outreachSchema } from '../../server/lib/schemas.js';
import { systemFor, userPayload } from '../../server/prompts/operationExitPrompts.js';
import { validateOutreach } from '../../server/lib/validate.js';
import { generateOutreachMessage } from '../../src/data/coachActions.js';

function wordCount(message = '') {
  return message.trim().split(/\s+/).filter(Boolean).length;
}

export default withApi({ name: 'outreach/generate', validate: validateOutreach }, async (body) => {
  if (hasOpenAI()) {
    try {
      const result = await structured(
        modelCheap(),
        systemFor('outreach'),
        userPayload(body),
        outreachSchema,
        'outreach_message'
      );
      // Recompute wordCount server-side to guarantee accuracy.
      return { ...result, wordCount: wordCount(result.message), fallback: false };
    } catch {
      // fall through
    }
  }
  const mock = generateOutreachMessage(body);
  return { ...mock, wordCount: wordCount(mock.message), fallback: true };
});
