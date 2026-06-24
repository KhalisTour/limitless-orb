import { withApi } from '../server/lib/withApi.js';
import { hasOpenAI } from '../server/lib/openaiClient.js';
import { driverName } from '../server/lib/store.js';

export default withApi({ method: 'GET', auth: false, name: 'health' }, async () => ({
  ok: true,
  openai: hasOpenAI(),
  store: await driverName(),
  time: new Date().toISOString(),
}));
