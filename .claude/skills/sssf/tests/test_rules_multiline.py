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

from adw_modules import agent_cc, agent_pi, rules

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
