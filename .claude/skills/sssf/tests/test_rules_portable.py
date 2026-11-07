"""A forbid pattern must read the same in Python and in pi's JavaScript extension.

The settings fail to load on Python regex syntax that JavaScript can't compile
or reads differently. One bun test shows each listed construct really does
disagree, so the list is grounded in the real extension.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_pi, repo_settings, rules, secret_files

PUSH_PATTERN = r"\bgit\b(\s+-[cC]\s+\S+|\s+--?[\w-]+(=\S+)?)*\s+push\b"

# (label, pattern, command Python matches, snippet of the message's construct, min Python)
CASES = [
    ("A", r"\Agit push", "git push", "`\\A`", None),
    ("Z", r"push\Z", "git push", "`\\Z`", None),
    ("z", r"push\z", "git push", "`\\z`", (3, 14)),
    ("bell", r"git\apush", "git\x07push", "`\\a`", None),
    ("U", r"git\U00000020push", "git push", "`\\U`", None),
    ("N", r"git\N{SPACE}push", "git push", "`\\N{...}`", None),
    ("named group", r"(?P<g>git) push", "git push", "named group", None),
    ("named backref", r"(?P<g>git) (?P=g)", "git git", "named group", None),
    ("comment", r"git(?#c) push", "git push", "comment group", None),
    ("flag i", r"(?i)git push", "GIT push", "inline flag group", None),
    ("flag x", r"(?x)git\ push", "git push", "inline flag group", None),
    ("flag s", r"(?s)git.push", "git\npush", "inline flag group", None),
    ("scoped flag", r"(?a:git) push", "git push", "inline flag group", None),
    ("conditional", r"(git)?(?(1)\spush|push)", "git push", "conditional group", None),
    ("atomic", r"(?>git) push", "git push", "atomic group", (3, 11)),
    ("possessive +", r"gi++t push", "giit push", "possessive", (3, 11)),
    ("possessive *", r"gi*+t push", "giit push", "possessive", (3, 11)),
    ("possessive ?", r"gi?+t push", "git push", "possessive", (3, 11)),
    ("possessive {}", r"gi{1,2}+t push", "giit push", "possessive", (3, 11)),
    ("no minimum", r"gi{,2}t push", "giit push", "no minimum", None),
    ("no minimum bare", r"gi{,}t push", "giit push", "no minimum", None),
    ("class bracket", r"[]x]", "]", "opening with `]`", None),
    ("negated class bracket", r"[^]x]", "a", "opening with `]`", None),
]
IDS = [case[0] for case in CASES]


def settings(tmp_path: Path, body: str) -> Path:
    path = tmp_path / repo_settings.PATH
    path.parent.mkdir(parents=True)
    path.write_text(body)
    return tmp_path


def skip_below(min_python):
    if min_python and sys.version_info < min_python:
        pytest.skip(f"Python {min_python} or later")


@pytest.mark.parametrize("on_match", ["stop", "refuse"])
@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_each_python_only_construct_fails_the_settings_in_both_modes(tmp_path, case, on_match):
    label, pattern, _, snippet, min_python = case
    skip_below(min_python)
    body = yaml.safe_dump({"agent_rules": [{"rule": "x", "forbid": [pattern], "on_match": on_match}]})
    with pytest.raises(ValidationError) as error:
        repo_settings.load(settings(tmp_path, body))
    if label == "z" and sys.version_info < (3, 14):
        return                      # Python itself rejects \z before 3.14
    assert repr(pattern) in str(error.value) or pattern in str(error.value)
    assert snippet in str(error.value)


def test_a_pattern_that_is_not_a_regex_keeps_its_message():
    with pytest.raises(ValidationError, match="not a valid regex"):
        rules.AgentRule(rule="x", forbid=["("])


def test_the_scanner_never_reads_past_the_end():
    for pattern in ("a\\\\", "\\[", "[a", "[^", "(?:", "a{", "a{1", "a{1,", "x*", "a\\{,2}"):
        try:
            re.compile(pattern)
        except re.error:
            continue
        assert rules._python_only(pattern) is None, pattern


PORTABLE = [
    r"\bjust\s+deploy\b", PUSH_PATTERN,
    r"\bjust\s+(fh-|fusion|plan\d)", r"(^|[;&|]\s*)pi\b", r"(^|[;&|]\s*)gh\s+",
    r"^git push", r"push$", r"(git) \1", r"[Gg]it", r"gi{0,2}t", r"[\]x]", r"(?:git)",
    r"(?=x)", r"(?!x)", r"(?<=x)", r"(?<!x)", r"a{2}", r"a*?", r"x{}",
    r"\{,2}", r"[+*?{,2}]", r"a\+\+", r"[(?i)]",
]


@pytest.mark.parametrize("on_match", ["stop", "refuse"])
def test_portable_patterns_still_load(tmp_path, on_match):
    for pattern in PORTABLE:
        assert rules.AgentRule(rule="x", forbid=[pattern], on_match=on_match).forbid == [pattern], pattern
    shipped = tmp_path / "shipped"
    (shipped / repo_settings.PATH).parent.mkdir(parents=True)
    shutil.copy(SKILL / "templates" / "repo.yaml", shipped / repo_settings.PATH)
    repo_settings.load(shipped)
    hub = SKILL.parents[2]
    if (hub / repo_settings.PATH).exists():
        loaded = repo_settings.load(hub).agent_rules
        assert any(isinstance(r, rules.AgentRule) and r.forbid for r in loaded)


SECRET_FILES = ["/r/.env", "/r/.env.local", "/r/my file.txt", "/r/a+b[1].txt",
                "/r/x{2}#~&.cfg", "/r/(p)|q^$.json"]


def test_the_built_in_secret_file_rule_passes():
    for name in SECRET_FILES:
        rule = secret_files.shell_rule([name])
        assert isinstance(rule, rules.AgentRule), name
        assert rules._python_only(rule.forbid[0]) is None, name


@pytest.mark.skipif(not shutil.which("bun"), reason="needs bun to load the pi extension")
def test_bun_shows_the_extension_and_python_disagree_on_each_construct(tmp_path):
    jobs = []
    for label, pattern, command, snippet, min_python in CASES:
        if min_python and sys.version_info < min_python:
            continue
        try:
            re.compile(pattern)
        except re.error:
            continue
        assert re.search(pattern, command, re.M), label
        found = rules._python_only(pattern)
        assert found and snippet in found[0], label
        rule = rules.AgentRule.model_construct(rule="r", forbid=[pattern], on_match="refuse")
        jobs.append({"label": label, "rules": rules.refusing([rule]), "command": command, "blocked": False})
    push = rules.AgentRule(rule="r", forbid=[PUSH_PATTERN], on_match="refuse")
    jobs.append({"label": "control", "rules": rules.refusing([push]), "command": "git push", "blocked": True})
    for name, command in (("my file", "cat 'my file.txt'"), ("a+b", "cat a+b[1].txt")):
        secret = secret_files.shell_rule(["/r/my file.txt", "/r/a+b[1].txt"])
        jobs.append({"label": name, "rules": rules.refusing([secret], builtin=secret),
                     "command": command, "blocked": True})

    check = tmp_path / "check.ts"
    check.write_text(f'''
import ext from {json.dumps(str(agent_pi.REFUSE_EXTENSION))};
import {{ readFileSync }} from "fs";
const cases = JSON.parse(readFileSync(process.argv[2], "utf8"));
const out = cases.map((c: any) => {{
  process.env.SSSF_REFUSE_RULES = JSON.stringify(c.rules);
  let handler: any;
  ext({{ on: (name: string, h: any) => {{ if (name === "tool_call") handler = h; }} }} as any);
  return handler({{ toolName: "bash", input: {{ command: c.command }} }}) ?? null;
}});
console.log(JSON.stringify(out));
''')
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(jobs))
    env = {k: v for k, v in os.environ.items() if k != "SSSF_REFUSE_RULES"}
    done = subprocess.run(["bun", str(check), str(cases)], capture_output=True, text=True,
                          env=env, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    for job, result in zip(jobs, json.loads(done.stdout)):
        if job["blocked"]:
            assert result and result["block"] is True, job["label"]
        else:
            assert result is None, job["label"]
