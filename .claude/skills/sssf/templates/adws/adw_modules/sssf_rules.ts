/**
 * SSSF's refuse mode for a repo's agent_rules, loaded into pi agents with -e.
 *
 * agent_pi passes the rules marked `on_match: refuse` in SSSF_REFUSE_RULES.
 * A bash call whose command matches one of their `forbid` patterns is blocked
 * before it runs, and the agent reads the rule as the call's result. The
 * patterns are Python regexes read here as JavaScript ones; one that doesn't
 * compile is skipped, and SSSF's stream check, which reads them as Python,
 * stops the run if that command runs.
 *
 * Like sssf_refuse.py, it searches what the command runs: executed() drops
 * heredoc bodies only when each feeds cat or tee and nothing runs code.
 * Patterns compile with the m flag, as the Python side searches with re.M, so
 * `^` matches at the start of every line.
 * Keep it in step with executed() in sssf_refuse.py, which rules.breach uses.
 */
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

// rules.REFUSED in Python: the stream check looks for it in the call's result.
const REFUSED = "SSSF refused this command";

interface Rule { rule: string; forbid: string[] }

const HEREDOC = /(?<!<)<<(?!<)(-?)[ \t]*(['"]?)([A-Za-z_][A-Za-z0-9_.-]*)\2/g;
const RUNS_CODE = new Set(["sh", "bash", "zsh", "dash", "ksh", "fish", "eval", "source", ".", "xargs", "exec",
  "python", "python3", "node", "bun", "deno", "tsx", "perl", "ruby", "php", "osascript"]);
const WRITERS = new Set(["cat", "tee"]);
const WRAPPERS = new Set(["env", "sudo", "nohup", "command", "time", "nice", "timeout", "uv", "npx", "bunx",
  "run"]);
const base = (word: string) => word.split("/").pop() ?? word;

function commandWords(line: string): string[] {
  const found: string[] = [];
  for (const segment of line.split(/[;&|(){}`]|\$\(/)) {
    let words = segment.split(/\s+/).filter(Boolean)
      .filter((w) => !/^[A-Za-z_][A-Za-z0-9_]*=/.test(w) && !/^-|^[0-9]+[smhd]?$/.test(w));
    while (words.length && WRAPPERS.has(base(words[0]))) words = words.slice(1);
    if (words.length) found.push(base(words[0]));
  }
  return found;
}

export function executed(command: string): string {
  const kept: string[] = [];
  const pending: [string, boolean][] = [];
  for (const line of command.split("\n")) {
    if (pending.length) {
      const [word, tabs] = pending[0];
      if ((tabs ? line.replace(/^\t+/, "") : line) === word) {
        pending.shift();
        kept.push(line);
      }
      continue;
    }
    kept.push(line);
    for (const m of line.matchAll(HEREDOC)) {
      const words = commandWords(line.slice(0, m.index));
      if (!words.length || !WRITERS.has(words[words.length - 1])) return command;
      pending.push([m[3], m[1] === "-"]);
    }
  }
  if (kept.some((line) => commandWords(line).some((w) => RUNS_CODE.has(w)))) return command;
  return kept.join("\n");
}

function compile(pattern: string): RegExp | null {
  try {
    return new RegExp(pattern, "m");
  } catch {
    return null;
  }
}

export default function (pi: ExtensionAPI) {
  const rules: Rule[] = JSON.parse(process.env.SSSF_REFUSE_RULES || "[]");
  const compiled = rules.map((r) => ({ rule: r.rule, patterns: r.forbid.map((p) => [p, compile(p)] as const) }));

  pi.on("tool_call", (event) => {
    if (event.toolName !== "bash") return;
    const command = executed(String((event.input as { command?: unknown })?.command ?? ""));
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
