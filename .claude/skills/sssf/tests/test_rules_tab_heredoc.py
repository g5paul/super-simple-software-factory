"""All three matchers see a split command in a tab-indented `<<-` heredoc.

A `<<-` heredoc strips each body line's leading tabs before the inner shell reads
the body, so `<tab>ju\\<newline><tab>st test live` runs `just test live`.
rules.breach, sssf_refuse.py and pi's extension (sssf_rules.ts) also search the
command with every backslash-newline and the tabs right after it removed. Every
row's outcome below was run under real bash (test_the_table_is_honest...).

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_cc, rules, secret_files
from test_rules import announce, claude_line, forwarder, ran, starter_push_rule
from test_rules_continuation import claude_events, needs_bun, stubs
from test_rules_multiline import _bun_handler, _pi_block

NL, BS, T = "\n", "\\", "\t"


def starter_deploy_rule():
    """The deploy rule from the starter's commented agent_rules in templates/repo.yaml, as shipped."""
    lines = (SKILL / "templates" / "repo.yaml").read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("#     - rule: Never run `just deploy`"))
    body = "\n".join(line[2:] for line in lines[start:start + 2])
    return rules.AgentRule(**{**yaml.safe_load(body)[0], "on_match": "refuse"})


PUSH = starter_push_rule()
DEPLOY = starter_deploy_rule()
SECRET = secret_files.shell_rule(["/r/.env"])
PROBE = rules.AgentRule(rule="no probe", forbid=["SSSF-REFUSE-PROBE"], on_match="refuse")
LIVE = rules.AgentRule(rule="no live tests", forbid=[r"\bjust\s+test\s+live\b"], on_match="refuse")
PI = rules.AgentRule(rule="no nested pi", forbid=[r"(^|[;&|]\s*)pi\b"], on_match="refuse")
RULES = {"PUSH": PUSH, "DEPLOY": DEPLOY, "SECRET": SECRET, "PROBE": PROBE, "LIVE": LIVE, "PI": PI}

JUST, PIRUN, PUSHED = ["just test live"], ["pi -p hi"], ["git push origin main"]


def heredoc(opener, *lines, indent=True):
    """An opener, the body lines (each tab-indented unless indent is False) and a tab-indented EOF."""
    return opener + NL + NL.join((T + line) if indent else line for line in lines) + NL + T + "EOF"


OPENERS = ["bash <<-'EOF'", "sh <<-'EOF'", "cat <<-'EOF' | bash"]

# (rule, command, matches, what real bash does: stub lines plus stdout)
ROWS = [
    *[("LIVE", heredoc(o, f"ju{BS}", "st test live"), True, JUST) for o in OPENERS],
    *[("DEPLOY", heredoc(o, f"ju{BS}", "st deploy"), True, ["just deploy"]) for o in OPENERS],
    *[("PUSH", heredoc(o, f"gi{BS}", "t push origin main"), True, PUSHED) for o in OPENERS],
    ("PUSH", heredoc("bash <<-'EOF'", f"git pu{BS}", "sh origin main"), True, PUSHED),
    *[("PROBE", heredoc(o, f"echo SSSF-REF{BS}", "USE-PROBE"), True, ["SSSF-REFUSE-PROBE"]) for o in OPENERS],
    ("SECRET", heredoc("bash <<-'EOF'", f"cat .e{BS}", "nv"), True, ["SECRET"]),
    ("PI", f"bash <<-'EOF'{NL}p{BS}{NL}{T}i -p hi{NL}{T}EOF", True, PIRUN),
    ("PI", heredoc("bash <<-'EOF'", f"true; p{BS}", "i -p hi"), True, PIRUN),
    ("LIVE", f"bash <<-'EOF'{NL}{T}ju{BS}{NL}{T}{T}{T}st test live{NL}{T}EOF", True, JUST),
    ("LIVE", heredoc("bash <<-'EOF'", f"ju{BS}", BS, "st test live"), True, JUST),
    ("LIVE", heredoc("bash <<-'EOF'", f"just test {BS}", "live"), True, JUST),
    ("LIVE", heredoc("bash <<-EOF", f"ju{BS}{BS}", "st test live"), True, JUST),
    ("LIVE", heredoc("sh <<-EOF", f"ju{BS}{BS}", "st test live"), True, JUST),
    # No heredoc, and the pinned ones.
    ("LIVE", f"just test{BS}{NL}{T}live", True, JUST),
    ("LIVE", f"just test {BS}{NL}{T}live", True, JUST),
    ("LIVE", f"ju{BS}{NL}{T}st test live", True, []),                       # accepted over-match
    ("SECRET", f"cat .e{BS}{NL}{T}nv", True, []),                           # accepted over-match
    ("LIVE", heredoc("bash <<-EOF", f"ju{BS}", "st test live"), True, []),  # accepted over-match
    # Known limits: a flip here means a limit was closed.
    ("PI", heredoc("bash <<-'EOF'", "pi -p hi"), False, PIRUN),
    ("PI", heredoc("bash <<-'EOF'", f"p{BS}", "i -p hi"), False, PIRUN),
    ("LIVE", heredoc("bash <<-'EOF'", f"# note{BS}", f"ju{BS}", "st test live"), False, JUST),
    ("LIVE", heredoc("bash <<-'EOF'", f"x{BS}{BS}", f"ju{BS}", "st test live"), False, JUST),
    # Spaces after a continuation are out of scope: the shell strips only tabs.
    ("LIVE", f"bash <<-'EOF'{NL}{T}ju{BS}{NL} {T}st test live{NL}{T}EOF", False, []),
    ("LIVE", f"bash <<-'EOF'{NL}{T}ju{BS}{NL}{T} st test live{NL}{T}EOF", False, []),
]
IDS = [f"{i}-{row[0]}" for i, row in enumerate(ROWS)]


def with_stubs(tmp_path):
    """The shared stubs plus a recording `git`, so a push row can never reach real git."""
    env, log = stubs(tmp_path)
    stub = Path(env["PATH"].split(":")[0]) / "git"
    stub.write_text(f'#!/bin/sh\necho "git $*" >> "{log}"\n')
    stub.chmod(0o755)
    return env, log


def run_prefix(tmp_path, line, rule_list, builtin=None):
    env, _ = with_stubs(tmp_path)
    env["SSSF_REFUSE_RULES"] = json.dumps(rules.refusing(rule_list, builtin))
    return subprocess.run([sys.executable, str(agent_cc.REFUSE_SHELL), line], capture_output=True,
                          text=True, env=env, cwd=tmp_path)


@pytest.mark.parametrize("name, command, expected, _runs", ROWS, ids=IDS)
def test_breach_reads_through_a_backslash_newline_in_a_tab_heredoc(name, command, expected, _runs):
    assert (rules.breach([RULES[name]], command) is not None) is expected


@pytest.mark.parametrize("name, command, expected, _runs", ROWS, ids=IDS)
def test_the_prefix_reads_through_a_backslash_newline_in_a_tab_heredoc(tmp_path, name, command, expected, _runs):
    done = run_prefix(tmp_path, claude_line(command), [RULES[name]])
    if expected:
        assert done.returncode == 126 and done.stderr.startswith(rules.REFUSED)
    else:
        assert done.returncode != 126 and rules.REFUSED not in done.stderr


@needs_bun
@pytest.mark.parametrize("name", list(RULES))
def test_pis_handler_reads_through_a_backslash_newline_in_a_tab_heredoc(tmp_path, name):
    rows = [row for row in ROWS if row[0] == name]
    blocks = _bun_handler(tmp_path, json.dumps(rules.refusing([RULES[name]])), [row[1] for row in rows])
    assert [bool((b or {}).get("block")) for b in blocks] == [row[2] for row in rows]


@pytest.mark.parametrize("name, command, _expected, runs", ROWS, ids=IDS)
def test_the_table_is_honest_about_what_bash_does(tmp_path, name, command, _expected, runs):
    env, log = with_stubs(tmp_path)
    (tmp_path / ".env").write_text("SECRET\n")
    done = subprocess.run(["bash", "-c", command], env=env, cwd=tmp_path, capture_output=True, text=True)
    recorded = log.read_text().splitlines() if log.exists() else []
    assert recorded + done.stdout.splitlines() == runs


# ── agreement ────────────────────────────────────────────────────────────────

RULE_A = rules.AgentRule(rule="rule A", forbid=[r"\bjust\s+test\s+live\b"], on_match="refuse")
RULE_B = rules.AgentRule(rule="rule B", forbid=[r"\bst\s+test\s+live\b"], on_match="refuse")
EXAMPLE = heredoc("bash <<-'EOF'", f"ju{BS}", "st test live")


@needs_bun
def test_all_three_name_the_first_rule_when_only_the_third_text_matches_it(tmp_path):
    assert not re.search(RULE_A.forbid[0], EXAMPLE) and re.search(RULE_B.forbid[0], EXAMPLE)
    found, _ = rules.breach([RULE_A, RULE_B], EXAMPLE)
    assert found is RULE_A
    done = run_prefix(tmp_path, claude_line(EXAMPLE), [RULE_A, RULE_B])
    assert "rule A" in done.stderr and "rule B" not in done.stderr
    [block] = _bun_handler(tmp_path, json.dumps(rules.refusing([RULE_A, RULE_B])), [EXAMPLE])
    assert "rule A" in block["reason"] and "rule B" not in block["reason"]


def test_the_prefix_refuses_the_example_in_lines_of_other_shapes(tmp_path):
    import shlex
    for line in (EXAMPLE, f"x; eval {shlex.quote(EXAMPLE)}"):
        done = run_prefix(tmp_path, line, [LIVE])
        assert done.returncode == 126 and done.stderr.startswith(rules.REFUSED)


@needs_bun
def test_a_split_push_in_a_tab_heredoc_is_a_counted_refusal_on_both_harnesses_and_a_breach_when_it_ran(tmp_path):
    rule = PUSH
    command = heredoc("bash <<-'EOF'", f"gi{BS}", "t push origin main")
    found = rules.breach([rule], command)
    assert found is not None
    [block] = _bun_handler(tmp_path, json.dumps(rules.refusing([rule])), [command])
    assert block["block"] is True
    forward, events, _ = forwarder(rule=rule)
    forward(announce("c1", command))
    forward(_pi_block("c1", block["reason"]))
    assert [e.name for e in events] == ["rule_refused"]

    done = run_prefix(tmp_path, claude_line(command), [rule])
    assert done.returncode == 126
    forward, events, _ = forwarder(rule=rule)
    for event in claude_events("t1", command, f"Exit code 126\n{done.stderr}"):
        forward(event)
    assert [e.name for e in events] == ["rule_refused"]

    forward, events, _ = forwarder(rule=rule)
    forward(announce("c2", command))
    with pytest.raises(rules.RuleBreach, match="may have run") as stop:
        forward(ran("c2", "done"))
    assert stop.value.pattern == found[1]
    assert json.dumps(found[1]) in done.stderr


@needs_bun
def test_the_built_in_secret_rule_names_itself_on_both_harnesses(tmp_path):
    command = heredoc("bash <<-'EOF'", f"cat .e{BS}", "nv")
    assert rules.breach([SECRET], command) is not None
    done = run_prefix(tmp_path, claude_line(command), [SECRET], SECRET)
    assert done.returncode == 126 and "secret-file rule" in done.stderr
    [block] = _bun_handler(tmp_path, json.dumps(rules.refusing([SECRET], SECRET)), [command])
    assert block["block"] is True


def test_a_stop_rule_stops_the_run_on_a_split_command_in_a_tab_heredoc():
    stop = rules.AgentRule(rule="no live tests", forbid=[r"\bjust\s+test\s+live\b"], on_match="stop")
    forward, events, _ = forwarder(rule=stop, can_refuse=False)
    with pytest.raises(rules.RuleBreach):
        forward(announce("c1", EXAMPLE))


@pytest.mark.parametrize("command", [(BS + NL + T) * 100_000, BS * 200_000, BS + NL + T * 200_000],
                         ids=["pairs", "backslashes", "tabs"])
def test_the_third_text_is_searched_in_linear_time(command):
    start = time.monotonic()
    assert rules.breach([LIVE], command) is None
    assert time.monotonic() - start < 5
