// The spans the account cards' spend tables can show, shared by server and UI.
const HOUR = 3_600_000;
const DAY = 24 * HOUR;

export const SPANS = ["5h", "24h", "7d", "30d", "60d", "90d"] as const;
export type Span = (typeof SPANS)[number];

export const SPAN_MS: Record<Span, number> = {
  "5h": 5 * HOUR, "24h": DAY, "7d": 7 * DAY, "30d": 30 * DAY, "60d": 60 * DAY, "90d": 90 * DAY,
};

export function isSpan(text: unknown): text is Span {
  return typeof text === "string" && (SPANS as readonly string[]).includes(text);
}
