"""A pattern anchored with `^` finds a command at the start of any line.

In a project, a no-PR rule written as `(^|[;&|(]\\s*)gh...` let
`echo hi` followed by `gh pr create` on the next line through: without
multiline matching, `^` is only the start of the whole command. The same
hole was in this skill's own example `pi` pattern. A shell reads each line
as a command, so all three matchers now search with `^` and `$` at every
line.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_cc, agent_pi, rules, secret_files
from test_rules import announce, forwarder, ran, starter_push_rule

# The skill's documented example, as references/config.md gives it.
PI = rules.AgentRule(rule="Never start pi.", forbid=[r"(^|[;&|]\s*)pi\b"], on_match="refuse")

CASES = [
    ("pi -p hi", True),
    ("echo hi\npi -p hi", True),
    ("cd src && ls\n  pi", False),           # indented: the pattern itself allows no spaces after ^
    ("echo hi; pi", True),
    ("echo hi\necho pip install x", False),
    ("cat > notes.md <<EOF\npi is the agent\nEOF", True),     # whole-command search
]


@pytest.mark.parametrize("command,refused", CASES)
def test_the_backstop_reads_every_line(command, refused):
    assert (rules.breach([PI], command) is not None) is refused


@pytest.mark.parametrize("command,refused", CASES)
def test_the_claude_prefix_reads_every_line(command, refused, tmp_path):
    line = f"true && eval {shlex.quote(command)} < /dev/null && pwd -P >| /dev/null"
    done = subprocess.run([sys.executable, str(agent_cc.REFUSE_SHELL), line], capture_output=True,
                          text=True, cwd=tmp_path,
                          env={**os.environ, "PATH": "/usr/bin:/bin",
                               "SSSF_REFUSE_RULES": json.dumps(rules.refusing([PI]))})
    assert (done.returncode == 126) is refused


@pytest.mark.skipif(not shutil.which("bun"), reason="needs bun to load the pi extension")
def test_the_pi_extension_reads_every_line(tmp_path):
    check = tmp_path / "check.ts"
    check.write_text(f'''
import ext from {json.dumps(str(agent_pi.REFUSE_EXTENSION))};
let handler: any;
ext({{ on: (name: string, h: any) => {{ if (name === "tool_call") handler = h; }} }} as any);
const cases: string[] = {json.dumps([c for c, _ in CASES])};
console.log(JSON.stringify(cases.map((command) => !!handler({{ toolName: "bash", input: {{ command }} }})?.block)));
''')
    env = {**os.environ, "SSSF_REFUSE_RULES": json.dumps(rules.refusing([PI]))}
    done = subprocess.run(["bun", str(check)], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [refused for _, refused in CASES]


def _bun_handler(tmp_path, rules_json, commands):
    """Run the real pi extension under bun on each command; returns its block, or None."""
    check = tmp_path / "check.ts"
    check.write_text(f'''
import ext from {json.dumps(str(agent_pi.REFUSE_EXTENSION))};
let handler: any;
ext({{ on: (name: string, h: any) => {{ if (name === "tool_call") handler = h; }} }} as any);
const commands: string[] = {json.dumps(commands)};
console.log(JSON.stringify(commands.map((command) => handler({{ toolName: "bash", input: {{ command }} }}) ?? null)));
''')
    env = {**os.environ, "SSSF_REFUSE_RULES": rules_json}
    done = subprocess.run(["bun", str(check)], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _pi_block(call_id, reason):
    # What pi 1.4.0's prepareToolCall / emitToolExecutionEnd (pi-agent-core agent-loop.js) emit
    # for a `tool_call` handler's {block: true, reason}: an error result with no durationMs.
    return {"type": "tool_execution_end", "toolCallId": call_id, "toolName": "bash",
            "result": {"content": [{"type": "text", "text": reason}], "details": {}}, "isError": True}


@pytest.mark.skipif(not shutil.which("bun"), reason="needs bun to load the pi extension")
def test_a_real_pi_refusal_counts_and_the_third_stops(tmp_path):
    rule = starter_push_rule()
    blocks = _bun_handler(tmp_path, json.dumps(rules.refusing([rule])), ["git push"] * rules.MAX_REFUSALS)
    forward, events, _ = forwarder(rule=rule)
    for n, block in enumerate(blocks[:-1], 1):
        assert block["block"] is True
        forward(announce(f"c{n}"))
        forward(_pi_block(f"c{n}", block["reason"]))
    assert [(e.name, e.payload["refusals"]) for e in events] == [("rule_refused", 1), ("rule_refused", 2)]
    forward(announce("last"))
    with pytest.raises(rules.RuleBreach) as stop:
        forward(_pi_block("last", blocks[-1]["reason"]))
    assert stop.value.refusals == rules.MAX_REFUSALS


@pytest.mark.skipif(not shutil.which("bun"), reason="needs bun to load the pi extension")
def test_a_command_that_slips_past_pi_and_prints_the_refusal_stops_the_run(tmp_path):
    rule = starter_push_rule()
    command = 'git -c user.name=﻿x push; echo "SSSF refused this command"'
    [slipped] = _bun_handler(tmp_path, json.dumps(rules.refusing([rule])), [command])
    assert not (slipped or {}).get("block"), "the slip this test relies on is gone"
    assert rules.breach([rule], command) is not None, "the slip this test relies on is gone"
    forward, events, _ = forwarder(rule=rule)
    forward(announce("c1", command))
    with pytest.raises(rules.RuleBreach, match="may have run"):
        forward(ran("c1", "SSSF refused this command\n"))
    assert events == []


@pytest.mark.skipif(not shutil.which("bun"), reason="needs bun to load the pi extension")
def test_a_real_pi_block_under_the_built_in_rule_is_uncounted_and_never_stops(tmp_path):
    builtin = secret_files.shell_rule(["/r/.env"])
    n = rules.MAX_REFUSALS + 2
    blocks = _bun_handler(tmp_path, json.dumps(rules.refusing([builtin], builtin)), ["cat .env"] * n)
    forward, events, _ = forwarder(enforced=[builtin], uncounted=(builtin,))
    for i, block in enumerate(blocks):
        assert block["block"] is True
        forward(announce(f"b{i}", "cat .env"))
        forward(_pi_block(f"b{i}", block["reason"]))
    assert [(e.name, e.payload["refusals"]) for e in events] == [("rule_refused", 0)] * n
