/**
 * SSSF's refuse mode for a repo's agent_rules, loaded into every pi agent with -e.
 * It also blocks `read`, `grep`, `find` and `ls` on a protected secret file,
 * from SSSF_SECRET_FILES (absolute paths found at agent start).
 *
 * agent_pi passes the rules marked `on_match: refuse` in SSSF_REFUSE_RULES.
 * A bash call whose command matches one of their `forbid` patterns is blocked
 * before it runs, and the agent reads the rule as the call's result. The
 * patterns are Python regexes read here as JavaScript ones; one that doesn't
 * compile is skipped, and SSSF's stream check, which reads them as Python,
 * stops the run if that command runs.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

// rules.REFUSED in Python: the stream check looks for it in the call's result.
const REFUSED = "SSSF refused this command";

interface Rule { rule: string; forbid: string[] }

// Patterns compile with the m flag, as the Python side searches with re.M, so
// `^` matches at the start of every line.
function compile(pattern: string): RegExp | null {
  try {
    return new RegExp(pattern, "m");
  } catch {
    return null;
  }
}

const real = (p: string): string | null => {
  try {
    return fs.realpathSync(p);
  } catch {
    return null;
  }
};

// A path as pi reads it: one leading @ dropped, ~ expanded, resolved from cwd.
function normalize(p: string): string {
  if (p.startsWith("@")) p = p.slice(1);
  if (p === "~") p = os.homedir();
  else if (p.startsWith("~/")) p = path.join(os.homedir(), p.slice(2));
  return path.resolve(process.cwd(), p);
}

export default function (pi: ExtensionAPI) {
  const secrets = new Set<string>();
  for (const file of JSON.parse(process.env.SSSF_SECRET_FILES || "[]") as string[]) {
    secrets.add(path.resolve(file));
    const resolved = real(file);
    if (resolved) secrets.add(resolved);
  }

  const rules: Rule[] = JSON.parse(process.env.SSSF_REFUSE_RULES || "[]");
  const compiled = rules.map((r) => ({ rule: r.rule, patterns: r.forbid.map((p) => [p, compile(p)] as const) }));

  pi.on("tool_call", (event) => {
    if (["read", "grep", "find", "ls"].includes(event.toolName)) {
      const given = (event.input as { path?: unknown })?.path;
      if (typeof given !== "string" || !given || secrets.size === 0) return;
      const wanted = normalize(given);
      const resolved = real(wanted);
      if (secrets.has(wanted) || (resolved && secrets.has(resolved))) {
        return {
          block: true,
          reason: `${REFUSED}: ${given} is one of this repo's secret files, which SSSF keeps from agents. `
            + "Nothing ran. Don't try it another way: do the task without it, or say in your "
            + "report that the task needs it.",
        };
      }
      return;
    }
    if (event.toolName !== "bash") return;
    const command = String((event.input as { command?: unknown })?.command ?? "");
    for (const { rule, patterns } of compiled) {
      for (const [source, re] of patterns) {
        if (re?.test(command)) {
          return {
            block: true,
            reason: `${REFUSED}, by this repo's rule: ${rule} (it matches ${JSON.stringify(source)}). `
              + "Nothing ran. Don't try it another way: do the task without it, or say in your "
              + "report that the task needs it.",
          };
        }
      }
    }
  });
}
