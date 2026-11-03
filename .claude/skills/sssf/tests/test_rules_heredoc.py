"""A forbidden command named inside a heredoc that only writes a file isn't run.

In a project's run, the planner wrote its plan with `cat > plan.md <<'EOF'`,
and the plan's last line said not to run the live test recipe. The repo's
refuse rule matched that line and refused the whole command, though it would
have run nothing. Every agent is shown the rules, so plans and write-ups name
them, and the third refusal in one call stops the run. All three matchers now
search `executed(command)`: the command without the bodies of heredocs that
only feed a file or a non-runner. A heredoc fed to a shell or an interpreter
is still searched, since it runs.

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
from adw_modules.sssf_refuse import executed

LIVE = rules.AgentRule(rule="Never run the live suite.", forbid=[r"\bjust\s+test\s+live\b"],
                       on_match="refuse")

PLAN = """mkdir -p session && cat > session/plan.md <<'EOF'
# Plan: fix the flaky test

## Verification

Run `just test unit`. Do not run any live or paid recipe (`just test live`).
EOF
mkdir -p specs && cp session/plan.md specs/ab12cd34_fix.md"""

# (command, refused?)
CASES = [
    (PLAN, False),
    ("cat <<EOF > notes.md\njust test live\nEOF", False),
    ('tee notes.md <<"END"\njust test live\nEND', False),
    ("cat <<-EOF > notes.md\n\tjust test live\n\tEOF", False),
    ("cat > a.md <<A && cat > b.md <<B\njust test live\nA\njust test live\nB", False),
    ("cat > notes.md <<EOF\nhi\nEOF\njust test live", True),
    ("bash <<EOF\njust test live\nEOF", True),
    ("sh -s <<'EOF'\njust test live\nEOF", True),
    ("python3 - <<EOF\nimport os; os.system('just test live')\nEOF", True),
    ("FOO=1 timeout 60 bash <<EOF\njust test live\nEOF", True),
    ("just test live", True),
    ("bash -c 'just test live'", True),
    ("cat <<< 'just test live'", True),
    ("cat > notes.md <<EOF\nnever closed\njust test live", False),
]


@pytest.mark.parametrize("command,refused", CASES)
def test_the_backstop_searches_only_what_runs(command, refused):
    assert (rules.breach([LIVE], command) is not None) is refused


def claude_line(command: str) -> str:
    return ("source /home/u/.claude/shell-snapshots/snapshot-bash-1.sh 2>/dev/null || true"
            f" && eval {shlex.quote(command)} < /dev/null && pwd -P >| /tmp/claude-ab12-cwd")


@pytest.mark.parametrize("command,refused", CASES)
def test_the_claude_prefix_agrees_with_the_backstop(command, refused, tmp_path):
    # Every case is refused or harmless: a command that isn't refused only
    # writes files under tmp_path, or fails for lack of a `just` recipe.
    done = subprocess.run([sys.executable, str(agent_cc.REFUSE_SHELL), claude_line(command)],
                          capture_output=True, text=True, cwd=tmp_path,
                          env={**os.environ, "SSSF_REFUSE_RULES": json.dumps(rules.refusing([LIVE])),
                               "PATH": "/usr/bin:/bin"})
    assert (done.returncode == 126 and done.stderr.startswith(rules.REFUSED)) is refused


@pytest.mark.skipif(not shutil.which("bun"), reason="needs bun to load the pi extension")
def test_the_pi_extension_agrees_with_the_backstop(tmp_path):
    check = tmp_path / "check.ts"
    check.write_text(f'''
import ext, {{ executed }} from {json.dumps(str(agent_pi.REFUSE_EXTENSION))};
let handler: any;
ext({{ on: (name: string, h: any) => {{ if (name === "tool_call") handler = h; }} }} as any);
const cases: string[] = {json.dumps([c for c, _ in CASES])};
console.log(JSON.stringify(cases.map((command) => ({{
  blocked: !!handler({{ toolName: "bash", input: {{ command }} }})?.block,
  text: executed(command),
}}))));
''')
    env = {**os.environ, "SSSF_REFUSE_RULES": json.dumps(rules.refusing([LIVE]))}
    done = subprocess.run(["bun", str(check)], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    for (command, refused), got in zip(CASES, json.loads(done.stdout)):
        assert got["blocked"] is refused, command
        assert got["text"] == executed(command), command      # the two ports drop the same lines


def test_a_plan_keeps_its_commands_and_loses_only_its_text():
    text = executed(PLAN)
    assert "mkdir -p session && cat > session/plan.md <<'EOF'" in text
    assert "cp session/plan.md specs/ab12cd34_fix.md" in text
    assert "Do not run" not in text
