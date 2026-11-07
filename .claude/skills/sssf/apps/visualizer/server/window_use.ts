/**
 * How fast a usage window fills, in percent per hour, from the readings kept
 * over time. Claude's come from rate_limits_history.jsonl, which account.py
 * appends to; ChatGPT's from codex_limits_history.jsonl, which codex_limits.ts
 * appends to. A line is {at, w: {five_hour: [utilization, resetsAt], ...}},
 * written when a window moves and at least hourly while one doesn't, so a
 * longer gap means nothing was recording.
 *
 * A window starts empty, so its start is a reading of 0 even with no line.
 */
import { existsSync, readFileSync } from "node:fs";
import type { LimitWindow, WindowUse } from "../shared/types.ts";

export type Kind = "five_hour" | "seven_day";
export const WINDOW_MS: Record<Kind, number> = { five_hour: 5 * 3_600_000, seven_day: 7 * 86_400_000 };
/** The same spans as the $ pace: the last hour of a 5-hour window, the last day of a weekly one. */
export const PACE_MS: Record<Kind, number> = { five_hour: 3_600_000, seven_day: 24 * 3_600_000 };
const GAP_MS = 90 * 60_000;
const MIN_MS = 15 * 60_000;
const hours = (ms: number) => ms / 3_600_000;

export interface Reading { at: number; w: Partial<Record<Kind, [number, number]>> }

export function readHistory(path: string): Reading[] {
  if (!existsSync(path)) return [];
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    return [];
  }
  const out: Reading[] = [];
  for (const line of text.split("\n")) {
    if (!line) continue;
    try {
      const d = JSON.parse(line);
      const at = Date.parse(d.at);
      if (!Number.isNaN(at) && d.w && typeof d.w === "object") out.push({ at, w: d.w });
    } catch {
      // a half-written line
    }
  }
  return out.toSorted((a, b) => a.at - b.at);
}

/** A closed 7-day window, as account.py and codex_limits.ts archive it; times in epoch seconds. */
export interface Archived { provider: "claude" | "chatgpt"; kind: "seven_day"; start: number; end: number; peak: number; last_at: string }

export function readArchive(path: string): Archived[] {
  if (!existsSync(path)) return [];
  const out: Archived[] = [];
  let text = "";
  try {
    text = readFileSync(path, "utf8");
  } catch {
    return [];
  }
  for (const line of text.split("\n")) {
    if (!line) continue;
    try {
      const d = JSON.parse(line);
      if ((d.provider === "claude" || d.provider === "chatgpt") && typeof d.end === "number" && typeof d.peak === "number") out.push(d);
    } catch {
      // a half-written line
    }
  }
  return out.toSorted((a, b) => b.end - a.end);
}

/** The window that closed when a reading names a new 7-day reset, from the readings of it; null when none closed. */
export function closedWeek(history: Reading[], next: Reading, provider: Archived["provider"]): Archived | null {
  const now = next.w.seven_day;
  const prev = history.findLast((r) => r.w.seven_day);
  if (!now || !prev) return null;
  const end = prev.w.seven_day![1];
  // An unused window can report a reset that moves; only one that has ended is closed.
  if (Math.abs(end - now[1]) < 300 || end * 1000 > next.at || end > now[1]) return null;
  const same = history.filter((r) => r.w.seven_day && Math.abs(r.w.seven_day[1] - end) < 300);
  return { provider, kind: "seven_day", start: end - 7 * 86_400, end, peak: Math.max(...same.map((r) => r.w.seven_day![0])),
    last_at: new Date(same.at(-1)!.at).toISOString() };
}

/** The window's use since it started, and over the recent span, as of the reading at `readAt`. */
export function windowUse(history: Reading[], kind: Kind, live: LimitWindow, readAt: number): WindowUse {
  const end = Date.parse(live.resets_at);
  const start = end - WINDOW_MS[kind];
  const now = live.utilization * 100;
  const sinceStart = readAt - start >= MIN_MS
    ? { pct_per_hour: now / hours(readAt - start), hours: hours(readAt - start) }
    : null;
  // Lines from this window: inside it, and naming the same reset to within a few minutes.
  const points: { at: number; pct: number }[] = [{ at: start, pct: 0 }];
  for (const r of history) {
    const w = r.w[kind];
    if (!w || r.at < start || r.at > readAt || Math.abs(w[1] * 1000 - end) > 5 * 60_000) continue;
    points.push({ at: r.at, pct: w[0] * 100 });
  }
  const spanStart = Math.max(start, readAt - PACE_MS[kind]);
  const base = points.findLast((p) => p.at <= spanStart) ?? points[0]!;
  // The base's figure held until the span began only if something was recording then.
  const from = spanStart - base.at <= GAP_MS ? spanStart : base.at;
  const recent = readAt - from >= MIN_MS
    ? { from: new Date(from).toISOString(), hours: hours(readAt - from), points: now - base.pct,
      pct_per_hour: (now - base.pct) / hours(readAt - from), usd: 0 }
    : null;
  return { since_start: sinceStart, recent };
}
