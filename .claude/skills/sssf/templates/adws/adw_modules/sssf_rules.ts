/**
 * SSSF's refuse mode for a repo's agent_rules, loaded into pi agents with -e.
 *
 * agent_pi passes the rules marked `on_match: refuse` in SSSF_REFUSE_RULES.
 * A bash call whose command matches one of their `forbid` patterns is blocked
 * before it runs, and the agent reads the rule as the call's result. The
 * patterns are Python regexes read here as JavaScript ones; one that doesn't
 * compile is skipped, and SSSF's stream check, which reads them as Python,
 * stops the run if that command runs.
 */
import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

// rules.REFUSED in Python: the stream check looks for it in the call's result.
const REFUSED = "SSSF refused this command";

interface Rule { rule: string; forbid: string[] }

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
