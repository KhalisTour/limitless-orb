// Shared higher-order wrapper applied to every endpoint handler.
// Order of operations (per spec):
//   1. method check (405)
//   2. optional owner-token auth (401)
//   3. in-memory fixed-window rate limit (429)
//   4. input validation (400)
//   5. run handler; sanitize any thrown error (500)
//   6. log only endpoint name + status + duration (never payloads)

const WINDOW_MS = 60_000;
const MAX_REQ = 60; // 60 req/min per IP.

// NOTE: this Map lives in module memory and therefore resets on every
// serverless cold start. That is acceptable for a single-user tool.
const hits = new Map();

function rateLimited(ip) {
  const now = Date.now();
  const rec = hits.get(ip);
  if (!rec || now > rec.reset) {
    hits.set(ip, { count: 1, reset: now + WINDOW_MS });
    return false;
  }
  rec.count += 1;
  return rec.count > MAX_REQ;
}

function getIp(req) {
  const fwd = req.headers['x-forwarded-for'];
  if (fwd) return String(fwd).split(',')[0].trim();
  return req.socket?.remoteAddress || 'local';
}

export function withApi({ method = 'POST', auth = true, validate, name = 'api' }, handler) {
  return async function wrapped(req, res) {
    const start = Date.now();
    const send = (status, body) => {
      res.status(status).json(body);
      // Log only endpoint + status + duration. Never log resume text,
      // job notes, or outreach context.
      console.log(`[api] ${name} ${status} ${Date.now() - start}ms`);
    };

    try {
      if (req.method !== method) return send(405, { error: 'method_not_allowed' });

      // Owner-token gate. When the env var is unset, the app is fully open.
      // HONESTY NOTE: when the client sends this token it lives in the browser
      // bundle, so this is a lightweight "keep randoms out" gate, NOT real auth.
      if (auth) {
        const token = process.env.OPERATION_EXIT_OWNER_TOKEN;
        if (token) {
          const header = req.headers['authorization'] || '';
          if (header !== `Bearer ${token}`) return send(401, { error: 'unauthorized' });
        }
      }

      if (rateLimited(getIp(req))) return send(429, { error: 'rate_limited' });

      const body = req.body || {};
      if (validate) {
        const validationError = validate(body);
        if (validationError) return send(400, { error: validationError });
      }

      const result = await handler(body, req);
      return send(200, result);
    } catch (err) {
      // Never leak stack traces or raw OpenAI errors.
      return send(500, { error: 'internal_error' });
    }
  };
}
