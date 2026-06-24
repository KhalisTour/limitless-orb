import { withApi } from '../../server/lib/withApi.js';
import { validateState } from '../../server/lib/validate.js';
import { loadState, deriveCounters } from '../../server/lib/store.js';

export default withApi({ name: 'state/load', validate: validateState }, async (body) => {
  const userId = body.userId || 'owner';
  const state = await loadState(userId);
  return { ok: true, state, derived: deriveCounters(state) };
});
