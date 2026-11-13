// The factory board's filters: the section tile picked, and which levels are open.

export type Level = "red" | "amber" | "ok";
export const LEVELS: readonly Level[] = ["red", "amber", "ok"];
/** Red and amber open, ok closed: the board opens on what needs looking at. */
export const DEFAULT_LEVELS: ReadonlySet<Level> = new Set<Level>(["red", "amber"]);

interface Leveled {
  section: string;
  level: Level;
}

export function visibleRows<T extends Leveled>(rows: T[], only: string | null, open: ReadonlySet<Level>): T[] {
  return rows.filter((r) => (!only || r.section === only) && open.has(r.level));
}

/** Every level's count in the picked section, closed levels included. */
export function levelCounts(rows: Leveled[], only: string | null): Record<Level, number> {
  const counts = { red: 0, amber: 0, ok: 0 };
  for (const r of rows) if (!only || r.section === only) counts[r.level] += 1;
  return counts;
}

/** A stored choice, "red,amber". "" is every level closed; null or nothing valid is the default. */
export function parseLevels(stored: string | null): ReadonlySet<Level> {
  if (stored === null) return DEFAULT_LEVELS;
  if (stored === "") return new Set();
  const picked = stored.split(",").filter((s): s is Level => (LEVELS as string[]).includes(s));
  return picked.length ? new Set(picked) : DEFAULT_LEVELS;
}
