/**
 * pi used outside SSSF: the sessions pi saves under its agent dir,
 * ~/.pi/agent/sessions, or $PI_CODING_AGENT_DIR/sessions. SSSF's own pi agents
 * run with --session-dir inside the run (agent_pi.py), so nothing here is
 * counted twice. Each assistant message carries pi's list-price cost, the same
 * figure the trace keeps for SSSF's agents.
 *
 * Read incrementally, like the Claude transcripts: a file is reread from where
 * it left off, and a message is counted once by its responseId, since a
 * forked session copies its parent's messages.
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import type { PiCallAt } from "./account.ts";

export function piSessionsDir(): string {
  const agent = process.env.PI_CODING_AGENT_DIR?.trim();
  return join(agent || join(homedir(), ".pi", "agent"), "sessions");
}

interface FileState {
  size: number;
  cwd: string;
  calls: Omit<PiCallAt, "project">[];
  cwds: string[];
}

/** One line of a session file, as a call, or the session's cwd from its header. */
export function parseLine(line: string): { cwd?: string; call?: Omit<PiCallAt, "project"> & { key: string } } {
  let d;
  try {
    d = JSON.parse(line);
  } catch {
    return {};
  }
  if (d?.type === "session" && typeof d.cwd === "string") return { cwd: d.cwd };
  const m = d?.type === "message" ? d.message : null;
  const u = m?.usage;
  if (m?.role !== "assistant" || !u) return {};
  const at = Date.parse(d.timestamp ?? "");
  if (Number.isNaN(at)) return {};
  return {
    call: {
      key: String(m.responseId ?? `${d.id}@${d.timestamp}`),
      at,
      kind: "interactive",
      model: m.provider ? `${m.provider}/${m.model}` : String(m.model ?? "?"),
      cost: Number(u.cost?.total ?? 0) || 0,
      input_tokens: (u.input ?? 0) + (u.cacheRead ?? 0) + (u.cacheWrite ?? 0),
      output_tokens: u.output ?? 0,
    },
  };
}

export class PiSessions {
  private readonly files = new Map<string, FileState & { keys: string[] }>();
  private readonly seen = new Set<string>();

  constructor(readonly dir: string = piSessionsDir()) {}

  /** Every call outside SSSF since `since`, placed in its project by the session's cwd. */
  calls(since: number, projectOf: (cwd: string) => string): PiCallAt[] {
    if (!existsSync(this.dir)) return [];
    for (const sub of readdirSync(this.dir, { withFileTypes: true })) {
      if (!sub.isDirectory()) continue;
      const folder = join(this.dir, sub.name);
      for (const name of readdirSync(folder)) {
        if (!name.endsWith(".jsonl")) continue;
        const path = join(folder, name);
        let size: number;
        let mtime: number;
        try {
          ({ size, mtimeMs: mtime } = statSync(path));
        } catch {
          continue;
        }
        if (mtime < since && !this.files.has(path)) continue;
        this.read(path, size);
      }
    }
    const placed = new Map<string, string>();
    const place = (cwd: string) => placed.get(cwd) ?? placed.set(cwd, projectOf(cwd)).get(cwd)!;
    return [...this.files.values()].flatMap((f) =>
      f.calls.flatMap((c, i) => (c.at >= since ? [{ ...c, project: place(f.cwds[i] || f.cwd) }] : [])));
  }

  private read(path: string, size: number): void {
    const state = this.files.get(path) ?? { size: 0, cwd: "", calls: [], cwds: [], keys: [] };
    if (size < state.size) {
      // Rewritten from the start: forget what it held.
      for (const k of state.keys) this.seen.delete(k);
      Object.assign(state, { size: 0, cwd: "", calls: [], cwds: [], keys: [] });
    }
    if (size === state.size) {
      this.files.set(path, state);
      return;
    }
    let text: string;
    try {
      text = readFileSync(path, "utf8").slice(state.size);
    } catch {
      return;
    }
    // Leave a half-written last line for the next read.
    const end = text.lastIndexOf("\n") + 1;
    for (const line of text.slice(0, end).split("\n")) {
      if (!line) continue;
      const { cwd, call } = parseLine(line);
      if (cwd) state.cwd = cwd;
      if (call && !this.seen.has(call.key)) {
        this.seen.add(call.key);
        state.keys.push(call.key);
        const { key: _key, ...rest } = call;
        state.calls.push(rest);
        state.cwds.push(state.cwd);
      }
    }
    state.size += Buffer.byteLength(text.slice(0, end), "utf8");
    this.files.set(path, state);
  }
}
