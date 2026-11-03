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
 * Like sssf_refuse.py, it searches what the command runs: executed() drops the
 * body of a heredoc that only feeds a file, such as a plan written with cat.
 * Keep it in step with executed() in sssf_refuse.py, which rules.breach uses.
 */
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

// rules.REFUSED in Python: the stream check looks for it in the call's result.
const REFUSED = "SSSF refused this command";

interface Rule { rule: string; forbid: string[] }

const HEREDOC = /(?<!<)<<(?!<)(-?)[ \t]*(['"]?)([A-Za-z_][A-Za-z0-9_.-]*)\2/g;
const RUNS_STDIN = new Set(["sh", "bash", "zsh", "dash", "ksh", "fish", "eval", "source", ".", "xargs",
  "python", "python3", "node", "bun", "deno", "perl", "ruby", "php", "osascript"]);
const WRAPPERS = new Set(["env", "sudo", "nohup", "command", "exec", "time", "nice", "timeout", "uv", "npx",
  "bunx", "run"]);
const base = (word: string) => word.split("/").pop() ?? word;

function feedsARunner(before: string): boolean {
  let words = (before.split(/[;&|(){}]/).pop() ?? "").split(/\s+/).filter(Boolean)
    .filter((w) => !/^[A-Za-z_][A-Za-z0-9_]*=/.test(w) && !/^-|^[0-9]+[smhd]?$/.test(w));
  while (words.length && WRAPPERS.has(base(words[0]))) words = words.slice(1);
  return !words.length || RUNS_STDIN.has(base(words[0]));
}

export function executed(command: string): string {
  const out: string[] = [];
  const pending: [string, boolean, boolean][] = [];
  for (const line of command.split("\n")) {
    if (pending.length) {
      const [word, tabs, keep] = pending[0];
      if ((tabs ? line.replace(/^\t+/, "") : line) === word) {
        pending.shift();
        out.push(line);
      } else if (keep) out.push(line);
      continue;
    }
    out.push(line);
    for (const found of line.matchAll(HEREDOC)) {
      pending.push([found[3], found[1] === "-", feedsARunner(line.slice(0, found.index))]);
    }
  }
  return out.join("\n");
}

function compile(pattern: string): RegExp | null {
  try {
    return new RegExp(pattern);
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
