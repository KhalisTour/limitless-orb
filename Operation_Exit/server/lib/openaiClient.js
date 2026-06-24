// OpenAI client + structured-output helper.
// The API key is read ONLY from process.env (server-side). It is never sent to the browser.
import OpenAI from 'openai';
import { zodResponseFormat } from 'openai/helpers/zod';

const apiKey = process.env.OPENAI_API_KEY;

// Lazily build a single client when a key is present. When absent, every
// AI endpoint falls through to its local mock fallback instead.
let client = null;
if (apiKey) {
  client = new OpenAI({ apiKey });
}

export function hasOpenAI() {
  return Boolean(apiKey);
}

// Default model names live ONLY here. Never hardcode a model name elsewhere.
export function model() {
  return process.env.OPENAI_MODEL || 'gpt-5-mini';
}

// Cheap tier defaults to the main model unless explicitly pointed at a nano model.
export function modelCheap() {
  return process.env.OPENAI_MODEL_CHEAP || model();
}

// Structured Outputs call. Schema adherence is guaranteed by the API via the
// SDK's zod helper, so there is NO manual JSON-repair / retry-parse loop here.
// The only failure paths are: missing key, network error, or model refusal —
// all of which the caller catches and resolves with the local mock fallback.
export async function structured(modelName, system, user, zodSchema, schemaName) {
  if (!client) throw new Error('no_openai_key');
  const completion = await client.chat.completions.parse({
    model: modelName,
    messages: [
      { role: 'system', content: system },
      { role: 'user', content: user },
    ],
    response_format: zodResponseFormat(zodSchema, schemaName),
  });
  const msg = completion.choices[0].message;
  if (msg.refusal) throw new Error('model_refusal');
  return msg.parsed;
}
