"""Every agent rule searches the whole shell command, heredoc bodies included.

Three versions of a heredoc skip were tried on one day, so that a plan
written with `cat > plan.md <<'EOF'` that names a forbidden command would not
be refused. Each tried to tell text from what the shell runs, and each let
through commands the whole-command search had caught: `cat <<EOF | bash`, a
script run by path, an unquoted delimiter's `$(...)`, a hook planted in
.git, a copy of `sh` named `cat`. Two projects' sessions and bypass-reviewer
found them. The search went back to the whole command. Text meant only as
text goes in a file written with the agent's file tool, which no rule reads,
and the writing agents' prompts say so.

Every case here names the forbidden command, so every one is refused, by the
stream check, the Claude Code prefix and the pi extension alike.

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

LIVE = rules.AgentRule(rule="Never run the live suite.", forbid=[r"\bjust\s+test\s+live\b"],
                       on_match="refuse")

PLAN = """mkdir -p session && cat > session/plan.md <<'EOF'
# Plan: fix the flaky test

## Verification

Run `just test unit`. Do not run any live or paid recipe (`just test live`).
EOF
mkdir -p specs && cp session/plan.md specs/ab12cd34_fix.md"""

CASES = [
    PLAN,
    "cat <<'EOF' > notes.md\njust test live\nEOF",
    "mkdir -p a/b && cat > a/b/plan.md <<'EOF'\njust test live\nEOF",
    "cat > a.md <<'A'\njust test live\nA\n\ntee b.md <<'B' >/dev/null\njust test live\nB",
    'tee notes.md <<"END"\njust test live\nEND',
    "cat <<-'EOF' > notes.md\n\tjust test live\n\tEOF",
    "cat <<EOF > notes.md\njust test live\nEOF",
    "cat > notes.md <<EOF\n$(just test live)\nEOF",
    "cat > notes.md <<EOF\nhi\nEOF\njust test live",
    "bash <<EOF\njust test live\nEOF",
    "sh -s <<'EOF'\njust test live\nEOF",
    "python3 - <<EOF\nimport os; os.system('just test live')\nEOF",
    "FOO=1 timeout 60 bash <<EOF\njust test live\nEOF",
    "just test live",
    "bash -c 'just test live'",
    "cat <<< 'just test live'",
    "cat > notes.md <<'EOF'\nnever closed\njust test live",
    "cat <<EOF | bash\njust test live\nEOF",
    "cat <<'X' | sh\njust test live\nX",
    "tee /tmp/x.sh <<EOF && sh /tmp/x.sh\njust test live\nEOF",
    "bash -c \"$(cat <<'EOF'\njust test live\nEOF\n)\"",
    "bash <(cat <<'EOF'\njust test live\nEOF\n)",
    "tee /dev/stdout <<EOF | bash\njust test live\nEOF",
    "cat > x.sh <<EOF\njust test live\nEOF\nbash x.sh",
    "ssh box <<EOF\njust test live\nEOF",
    "pi <<EOF\njust test live\nEOF",
    "<<EOF cat\njust test live\nEOF",
    "cat > x.sh <<'EOF'\njust test live\nEOF\nchmod +x x.sh && ./x.sh",
    "cat > Makefile <<'EOF'\ngo:\n\tjust test live\nEOF\nmake go",
    "cat > conftest.py <<'EOF'\nimport os; os.system('just test live')\nEOF\npytest -q",
    "cat > .git/hooks/pre-commit <<'EOF'\n#!/bin/sh\njust test live\nEOF",
    "cat > ./.git/hooks/pre-commit <<'EOF'\njust test live\nEOF",
    "cat <<'EOF' | ssh box\njust test live\nEOF",
    "cat <<'EOF' | 'bash'\njust test live\nEOF",
    "cat <<'EOF' | $SHELL\njust test live\nEOF",
    "cat <<'EOF' | sudo -u root bash\njust test live\nEOF",
    "x=$(cat <<'EOF'\njust test live\nEOF\n); $x",
    "cat '<<EOF'\njust test live\nEOF",
    "cat x #<<EOF\njust test live\nEOF",
    "cat \\<<EOF\njust test live\nEOF",
    "echo 'x\ncat > a <<'EOF'\n'; just test live\nEOF",
    "cat > a <<'EOF' \\\n&& just test live\nEOF",
    "cat > a <<\"EOF\"x\nEOFx\njust test live\nEOF",
    "cat > a <<E\"OF\"\nEOF\njust test live\nE",
    "cat > a <<'E'OF\nhi\nEOF\njust test live\nE",
    "cat > a <<'EOF'\r\nx\r\nEOF\r\njust test live\r\n",
    "cat > p.md <<'EOF'\njust test live\nEOF\ncd /r && ls specs 2>/dev/null; mkdir -p specs && cp \"p.md\" \"specs/a b.md\" && ls specs",
    "cat > p.md <<'EOF'\njust test live\nEOF\ncp p.md .git/hooks/pre-commit",
    "cat > p.md <<'EOF'\njust test live\nEOF\ncd .git && cp ../p.md hooks/pre-commit",
    "cat > p.md <<'EOF'\njust test live\nEOF\ncp \"$(sh p.md)\" x",
    "cat > p.md <<'EOF'\njust test live\nEOF\nls | sh",
    "cat > p.md <<'EOF'\njust test live\nEOF\nls > x.sh",
]


@pytest.mark.parametrize("command", CASES)
def test_the_backstop_searches_the_whole_command(command):
    assert rules.breach([LIVE], command) is not None


def claude_line(command: str) -> str:
    return ("source /home/u/.claude/shell-snapshots/snapshot-bash-1.sh 2>/dev/null || true"
            f" && eval {shlex.quote(command)} < /dev/null && pwd -P >| /tmp/claude-ab12-cwd")


@pytest.mark.parametrize("command", CASES)
def test_the_claude_prefix_refuses_it_too(command, tmp_path):
    done = subprocess.run([sys.executable, str(agent_cc.REFUSE_SHELL), claude_line(command)],
                          capture_output=True, text=True, cwd=tmp_path,
                          env={**os.environ, "SSSF_REFUSE_RULES": json.dumps(rules.refusing([LIVE])),
                               "PATH": "/usr/bin:/bin"})
    assert done.returncode == 126 and done.stderr.startswith(rules.REFUSED), command


@pytest.mark.skipif(not shutil.which("bun"), reason="needs bun to load the pi extension")
def test_the_pi_extension_refuses_it_too(tmp_path):
    check = tmp_path / "check.ts"
    check.write_text(f'''
import ext from {json.dumps(str(agent_pi.REFUSE_EXTENSION))};
let handler: any;
ext({{ on: (name: string, h: any) => {{ if (name === "tool_call") handler = h; }} }} as any);
const cases: string[] = {json.dumps(CASES)};
console.log(JSON.stringify(cases.map((command) => !!handler({{ toolName: "bash", input: {{ command }} }})?.block)));
''')
    env = {**os.environ, "SSSF_REFUSE_RULES": json.dumps(rules.refusing([LIVE]))}
    done = subprocess.run(["bun", str(check)], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == [True] * len(CASES)


@pytest.mark.parametrize("agent", ["planner", "builder", "ticket_builder", "documenter", "scout", "reviewer", "standards_reviewer", "spec_reviewer"])
def test_writing_agents_write_files_with_their_file_tool(agent):
    text = (SKILL / "templates" / "prompt_engineering" / agent / "system.md").read_text()
    assert "file tool" in text and "heredoc" in text
