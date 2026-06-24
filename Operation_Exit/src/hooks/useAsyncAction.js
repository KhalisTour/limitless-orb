import { useCallback, useState } from 'react';

// Shared loading/error/fallback hook so every async call site behaves
// identically. run(apiFn, fallbackFn, ...args):
//   - set loading=true
//   - try apiFn(...args) -> return result (usedFallback reflects result.fallback)
//   - catch -> return fallbackFn(...args), usedFallback=true, error set
//   - finally loading=false
export function useAsyncAction() {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [usedFallback, setUsedFallback] = useState(false);

  const run = useCallback(async (apiFn, fallbackFn, ...args) => {
    setLoading(true);
    setError(null);
    try {
      const result = await apiFn(...args);
      // The server may itself have fallen back to the local mock (fallback:true).
      setUsedFallback(Boolean(result && result.fallback));
      return result;
    } catch (err) {
      setError(err?.message || 'request_failed');
      setUsedFallback(true);
      return fallbackFn(...args);
    } finally {
      setLoading(false);
    }
  }, []);

  return { run, loading, error, usedFallback };
}
