import { withApi } from '../../server/lib/withApi.js';
import { validateState } from '../../server/lib/validate.js';
import { saveState } from '../../server/lib/store.js';

export default withApi({ name: 'state/save', validate: validateState }, async (body) => {
  const userId = body.userId || 'owner';
  const patch = body.patch || {};
  // saveState honors the Vercel no-op behavior and returns { persisted, data, reason? }.
  const result = await saveState(userId, patch);
  return { ok: true, ...result };
});
