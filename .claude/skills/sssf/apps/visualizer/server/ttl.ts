/**
 * A result per key, reused until it is `ms` old. Bun's SQLite calls block the
 * server, so a slow read repeated on every poll holds up every other request.
 * A read that throws leaves nothing cached.
 */
export function ttlCache<T>(ms: number, now: () => number = Date.now): (key: string, read: () => T) => T {
  const kept = new Map<string, { at: number; value: T }>();
  return (key, read) => {
    const hit = kept.get(key);
    const at = now();
    if (hit && at - hit.at < ms) return hit.value;
    const value = read();
    kept.set(key, { at, value });
    return value;
  };
}
