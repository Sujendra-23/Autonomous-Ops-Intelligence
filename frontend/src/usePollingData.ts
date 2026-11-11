import { useCallback, useEffect, useState } from "react";

// Refresh in place so a live meeting can be read without resetting the page.
export function usePollingData<T>(load: (signal: AbortSignal) => Promise<T>, interval = 10000) {
  const [snapshot, setSnapshot] = useState<{ load: typeof load; value: T } | null>(null);
  const [failure, setFailure] = useState<{ load: typeof load; message: string } | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision(value => value + 1), []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function update() {
      setLoading(true);
      try {
        const result = await load(controller.signal);
        if (!controller.signal.aborted) { setSnapshot({ load, value: result }); setFailure(null); }
      } catch (cause) {
        if (!controller.signal.aborted) setFailure({ load, message: cause instanceof Error ? cause.message : "Could not load workspace data." });
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          timer = setTimeout(update, interval);
        }
      }
    }
    void update();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [load, interval, revision]);

  // Hide data from a previous route before the next effect starts.
  return { data: snapshot?.load === load ? snapshot.value : null, error: failure?.load === load ? failure.message : "", loading, refresh };
}
