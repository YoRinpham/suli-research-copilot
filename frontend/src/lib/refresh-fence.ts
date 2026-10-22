/** Invalidate reads both before a write and after its outcome becomes known. */
export async function withRefreshFence<T>(epoch: { current: number }, action: () => Promise<T>): Promise<T> {
  epoch.current++;
  try { return await action(); }
  finally { epoch.current++; }
}
