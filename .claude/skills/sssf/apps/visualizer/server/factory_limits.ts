/** The factory board's thresholds, in one block. factory.ts re-exports them. */
export const GIB = 1024 ** 3;
export const MEM_AMBER = 3 * GIB, MEM_RED = 1.5 * GIB;            // MemAvailable below
export const STORE_AMBER = 15 * GIB, STORE_RED = 25 * GIB;        // store_bytes + audit_bytes at or above
export const PRUNE_AMBER_H = 3, PRUNE_RED_H = 6;                  // last_prune older than
export const POLL_AMBER_S = 1, POLL_RED_S = 3;                    // slowest_poll_seconds above
export const SLOW_AMBER = 2, SLOW_RED = 3;                        // phase/run ratio to p90, at or above
export const COST_AMBER = 2;                                      // cost ratio to p90, at or above
export const RUN_WINDOW_MS = 24 * 3600 * 1000;
export const SCRIPT_TTL_MS = 30_000;
export const CLK_TCK = 100;
export const PRUNE_STATUS = "/var/lib/sssf-agentsh-prune/status.json";
