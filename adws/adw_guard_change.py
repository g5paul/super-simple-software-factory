#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Guard Change: plan, review the spec, build, verify, and hunt bypasses.

Hub-only. A change to SSSF's guard code goes through this chain instead of a
hand-run bypass review. docs/designs/hub-guard-chain.md is the design and
docs/postmortems/git-dir-guard.md is why it looks like this.

Usage:
    uv run adws/adw_guard_change.py "<prompt or path/to/prompt.md>" [--base main] [--allow-false-positives] [--adw-id a1b2c3d4] [--worktree]

Phases: engineer(request) -> planner -> guard_spec_reviewer [-> planner(revise) ... bounded]
        -> git(commit_plan) -> builder -> code(verify) [-> builder(fix) ... bounded]
        -> bypass_reviewer [-> builder(fix) -> code(verify) -> code(mechanism check)
                            -> bypass_reviewer ... at most MAX_ROUNDS rounds]
        -> code(report) -> git(commit_build)

The chain runs under main's guard: adws/ was stamped from main, and the run
changes the skill in .claude/skills/sssf/. The reviewer judges a snapshot
commit of the working tree, so it unpacks base and head from git and never
imports from a live folder.

Only a bypass or a harm against the baseline blocks, plus a new false
positive unless --allow-false-positives. Broken promises and old holes go in
the report. Two things stop the loop and hand the run to the engineer: a
fourth round, and a fix that adds a mechanism (new module-level state or a
new `global`, found by code, or the reviewer's `adds_mechanism`). There is no
merge: the run leaves the branch with its commits.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from adw_modules import agents, gates, git_helper, plan_checks, quality, sandbox, session, utils
from adw_modules.data_types import (AgentCall, BuildOutput, EnvelopeBase, GateReport,
                                    PhaseParams, PlanOutput, ReviewOutput)

REQUIRED_AGENTS = ["planner", "guard_spec_reviewer", "builder", "bypass_reviewer"]
MAX_SPEC_ROUNDS = 3   # the third only when the review before it blocked on fewer items
MAX_FIX_LOOPS = 3
MAX_ROUNDS = 3

GUARD_DIR = ".claude/skills/sssf/templates/adws/adw_modules"
GUARD_MODULES = ["rules.py", "sssf_refuse.py", "sssf_rules.ts", "secret_files.py", "permissions.py",
                 "agent_cc.py", "agent_pi.py", "gates.py"]

SPEC_SECTIONS = ["Threat model", "Baseline", "Bar", "Out of scope"]

DEFAULT_BAR = ("no agent file kept, no agent code run, and no engineer file deleted or "
               "overwritten, where the baseline does otherwise")


# ── the reviewer's output ────────────────────────────────────────────────────

class Finding(BaseModel):
    """One candidate the reviewer judged, with its verdict on both versions."""

    candidate: str                  # a Python string literal, so newlines and quotes are exact
    check: str                      # the check it passes or trips: breach, refusal, enforce, ...
    base: str = ""                  # the baseline's verdict
    head: str = ""                  # head's verdict
    diff_line: str = ""             # the line in the diff that lets it through, when there is one
    note: str = ""
    new_to_known_limits: bool = False   # old holes only: missing from references/config.md


class BypassReview(EnvelopeBase):
    """The bypass reviewer's verdict on one head, sorted against the spec's bar."""

    bypasses: list[Finding] = Field(default_factory=list)
    harms: list[Finding] = Field(default_factory=list)
    broken_promises: list[Finding] = Field(default_factory=list)
    old_holes: list[Finding] = Field(default_factory=list)
    false_positives: list[Finding] = Field(default_factory=list)
    adds_mechanism: bool = False
    mechanism: str = ""             # which state, mode or exception list, when adds_mechanism
    candidates: int = 0
    checks: int = 0


def review_well_formed(envelope: BypassReview, run) -> GateReport:
    """What the reviewer's prompt asks for, as far as code can see. A failure
    goes back to the reviewer: it is a malformed report, not a bypass."""
    report = GateReport()
    for kind in ("bypasses", "harms", "broken_promises", "old_holes", "false_positives"):
        for i, f in enumerate(getattr(envelope, kind)):
            report.check(f"{kind}[{i}]", bool(f.candidate.strip() and f.check.strip()),
                         "names its candidate and check" if f.candidate.strip() and f.check.strip()
                         else "give the candidate as a string literal and the check it meets")
            if kind in ("bypasses", "harms", "false_positives"):
                both = bool(f.base.strip() and f.head.strip())
                report.check(f"{kind}[{i}] verdicts", both, "base and head given" if both else
                             "give the verdict on base and on head")
    report.check("mechanism", not envelope.adds_mechanism or bool(envelope.mechanism.strip()),
                 "named" if envelope.adds_mechanism else "none flagged")
    report.check("counts", envelope.candidates > 0 and envelope.checks > 0,
                 f"{envelope.candidates} candidates, {envelope.checks} checks"
                 if envelope.candidates > 0 and envelope.checks > 0
                 else "report how many candidates you tried and how many checks you ran")
    return report


def blocking(review: BypassReview, allow_false_positives: bool = False) -> list[Finding]:
    """The findings that keep a round from passing: no_bypass, then
    no_false_positive unless the engineer turned it off."""
    found = review.bypasses + review.harms
    return found if allow_false_positives else found + review.false_positives


# ── the spec ─────────────────────────────────────────────────────────────────

def _sections(text: str) -> dict[str, str]:
    """Each `##` heading's body, keyed by the heading in lower case."""
    parts = re.split(r"^##\s+(.+?)\s*$", text, flags=re.MULTILINE)
    return {parts[i].strip().lower(): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def spec_sections(base_sha: str):
    """Gate factory: the spec has each section, with words in it, and its
    Baseline names the commit the change is judged against."""

    def gate(envelope: EnvelopeBase, run) -> GateReport:
        report = GateReport()
        plan = [a for a in envelope.artifacts if Path(a).name == "plan.md"]
        if not plan or not sandbox.current().is_file(Path(plan[0])):
            return report.check("plan.md", False, "declare the plan.md you wrote in artifacts")
        found = _sections(sandbox.current().read_text(Path(plan[0])))
        for name in SPEC_SECTIONS:
            body = next((b for h, b in found.items() if h.startswith(name.lower())), None)
            words = len(body.split()) if body else 0
            report.check(name, words >= 5, f"{words} words" if body is not None and words >= 5
                         else f"add a '## {name}' section that says it in full")
        baseline = next((b for h, b in found.items() if h.startswith("baseline")), "")
        named = base_sha[:7] in baseline
        report.check("baseline commit", named, f"names {base_sha[:7]}" if named else
                     f"the Baseline section must name the commit {base_sha[:7]}")
        return report

    gate.__name__ = "spec_sections"
    return gate


def plan_brief(request: str, base_label: str, base_sha: str) -> str:
    return f"""{request}

## How to plan a guard change

This is a change to SSSF's guard code, in `{GUARD_DIR}/`. Read each guard module the
request touches whole, `.claude/skills/sssf/references/config.md`, and the tests that
cover it, before you write.

The spec must have these four `##` sections, besides the usual ones:

- `## Threat model`: what the agent can do, and what this guard can't stop at its
  level, since the agent runs as the engineer's own user.
- `## Baseline`: `{base_label}` at `{base_sha[:7]}`. Name that commit. Every finding
  is judged against it.
- `## Bar`: what the change must do and must never do against the baseline. Unless
  the request says otherwise: {DEFAULT_BAR}.
- `## Out of scope`: the holes this change leaves open, so a reviewer files them as
  old holes.

Plan the smallest change that meets the bar. A guard test builds its config from
the `sssf.config.yaml` SSSF ships, under `.claude/skills/sssf/templates/`, never a
test-only one."""


def build_brief(spec: str) -> str:
    return f"""Build the guard change the spec at `{spec}` describes.

Add a test for each case the change fixes. A guard test builds its config from the
`sssf.config.yaml` SSSF ships, under `.claude/skills/sssf/templates/`, never a
test-only one.

When you fix a finding, narrow the existing code: tighten the check, the path or the
condition that let the case through. If the only fix you can see adds module-level
state, a new mode, or a new list of exceptions, build it and name it in
`notes_for_next_agent`. The chain stops there and hands the choice to the engineer."""


def review_brief(spec: str, base_label: str, base_sha: str, head_sha: str,
                 previous_head: str | None, round_: int, suite: str) -> str:
    fix = (f"This head is a fix for round {round_ - 1}'s findings. The head that round "
           f"reviewed is `{previous_head}`: `git diff {previous_head} {head_sha}` is the fix."
           if previous_head else "This is the first round: the head is the whole build.")
    return f"""Review the guard change the spec at `{spec}` describes.

- base: `{base_sha}` ({base_label}), the spec's baseline
- head: `{head_sha}`, a snapshot commit of the build
- round: {round_} of at most {MAX_ROUNDS}
- the hub suite on head: {suite}

{fix}"""


# ── mechanisms ───────────────────────────────────────────────────────────────

def _show(sha: str, path: str) -> str:
    result = sandbox.current().run(["git", "-c", "core.fsmonitor=", "show", f"{sha}:{path}"])
    return result.stdout if result.returncode == 0 else ""


def _py_state(source: str) -> tuple[set[str], set[str]]:
    """Module-level names a module assigns, and every name it declares global."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set(), set()
    names: set[str] = set()

    def top(body) -> None:
        for node in body:
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            names.add(n.id)
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                top(node.body)
                top(getattr(node, "orelse", []))
                for h in getattr(node, "handlers", []):
                    top(h.body)
                top(getattr(node, "finalbody", []))

    top(tree.body)
    declared = {name for n in ast.walk(tree) if isinstance(n, ast.Global) for name in n.names}
    return names, declared


TS_STATE = re.compile(r"^(?:export\s+)?(?:let|var)\s+([A-Za-z_$][\w$]*)", re.MULTILINE)


def new_state(old: str, new: str, path: str) -> list[str]:
    """What `new` adds over `old`: module-level names and `global`s in
    Python, top-level `let` and `var` in TypeScript."""
    if path.endswith(".ts"):
        return [f"{path}: top-level {n}" for n in sorted(set(TS_STATE.findall(new))
                                                        - set(TS_STATE.findall(old)))]
    old_names, old_globals = _py_state(old)
    new_names, new_globals = _py_state(new)
    return ([f"{path}: module-level {n}" for n in sorted(new_names - old_names)]
            + [f"{path}: global {n}" for n in sorted(new_globals - old_globals)])


def mechanisms(old_sha: str, new_sha: str) -> list[str]:
    """Every piece of state the guard modules gained from old_sha to new_sha."""
    found: list[str] = []
    for name in GUARD_MODULES:
        path = f"{GUARD_DIR}/{name}"
        found += new_state(_show(old_sha, path), _show(new_sha, path), path)
    return found


# ── snapshots ────────────────────────────────────────────────────────────────

def snapshot(message: str, cwd: str | None = None) -> str:
    """A commit of the working tree, untracked files included, made through a
    throwaway index. The tree, the index and every ref stay as they were."""
    box = sandbox.current()
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}

        def git(*args: str) -> str:
            result = box.run(["git", "-c", "core.fsmonitor=", "-c", "core.hooksPath=/dev/null",
                              "-c", "user.name=sssf", "-c", "user.email=sssf@localhost", *args],
                             cwd=cwd, env=env)
            if result.returncode != 0:
                raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
            return result.stdout.strip()

        git("read-tree", "HEAD")
        git("add", "-A")
        return git("commit-tree", git("write-tree"), "-p", "HEAD", "-m", message)


# ── the report ───────────────────────────────────────────────────────────────

def write_report(path: Path, review: BypassReview | None, outcome: str,
                 heads: list[str], found_state: list[str]) -> None:
    def section(title: str, items: list[Finding]) -> list[str]:
        lines = [f"## {title} ({len(items)})", ""]
        for f in items:
            extra = " New to the known limits." if f.new_to_known_limits else ""
            lines.append(f"- `{f.candidate}` passes {f.check}. Base: {f.base or '-'}. "
                         f"Head: {f.head or '-'}. {f.diff_line} {f.note}{extra}".rstrip())
        return lines + [""]

    lines = ["# Guard change report", "", f"Outcome: {outcome}", "",
             "Heads reviewed: " + (", ".join(f"`{h[:12]}`" for h in heads) or "none"), ""]
    if found_state:
        lines += ["New module-level state in the guard modules:", ""]
        lines += [f"- {s}" for s in found_state] + [""]
    if review is not None:
        if review.adds_mechanism:
            lines += [f"The reviewer flagged a new mechanism: {review.mechanism}", ""]
        for title, items in (("Bypasses", review.bypasses), ("Harms", review.harms),
                             ("False positives", review.false_positives),
                             ("Broken promises", review.broken_promises),
                             ("Old holes", review.old_holes)):
            lines += section(title, items)
        lines.append(f"{review.candidates} candidates, {review.checks} checks.")
    path.write_text("\n".join(lines) + "\n")


# ── the chain ────────────────────────────────────────────────────────────────

def main(prompt: str, config: str = "adws/adw_sssf_config/sssf.config.yaml",
         adw_id: str | None = None, worktree: bool = False, base: str = "main",
         allow_false_positives: bool = False) -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)
    if not worktree and not adw_id and git_helper.current_branch() in ("main", "master"):
        raise SystemExit("a guard change runs on its own branch: cut one from main first, "
                         "or pass --worktree")
    run = session.ensure(cfg, adw_id, use_worktree=worktree, repo_sandbox=True)
    base_sha = git_helper.rev(base)
    cwd = str(run.repo_root)

    def commit(ph, envelope) -> None:
        message = envelope.commit_message or f"sssf({run.adw_id}): {envelope.summary}"
        ph.log(sha=git_helper.commit_all(message), message=message)

    def record(ph, result) -> None:
        ph.log(passed=result.passed, checks=result.tally(),
               artifacts=", ".join(result.artifacts))

    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Capture the incoming ask")) as ph:
        ph.log(input=prompt, baseline=f"{base} @ {base_sha[:7]}")

    planned = plan_brief(prompt, base, base_sha)
    plan_gates = gates.PLAN + [spec_sections(base_sha)]
    with run.phase(PhaseParams(name="plan", kind="agent", owner="planner", retries=1,
                               description="Write the spec: threat model, baseline, bar, "
                                           "out of scope")) as ph:
        plan = ph.call(AgentCall(output_type=PlanOutput, prompt=planned, gates=plan_gates))

    spec_ok = False
    blocked = None                        # how many items the last review blocked on
    for i in range(1, MAX_SPEC_ROUNDS + 1):
        with run.phase(PhaseParams(name=f"spec_review_{i}", kind="agent",
                                   owner="guard_spec_reviewer",
                                   description="Check the spec before any code: four sections, "
                                               "concrete, and no bigger than the bar needs")) as ph:
            spec_review = ph.call(AgentCall(output_type=ReviewOutput, prompt=planned,
                                            previous=plan,
                                            gates=[gates.artifacts_exist,
                                                   gates.verdict_consistent]))
        if spec_review.approved:
            spec_ok = True
            break
        now = max(len(spec_review.blocking), 1)
        if i == MAX_SPEC_ROUNDS or (i >= 2 and now >= blocked):
            break
        blocked = now
        with run.phase(PhaseParams(name=f"revise_plan_{i}", kind="agent", owner="planner",
                                   retries=1, description="Close the spec review's findings")) as ph:
            plan = ph.call(AgentCall(output_type=PlanOutput, prompt=planned,
                                     previous=spec_review, gates=plan_gates))

    if not spec_ok:
        return run.finish(accepted=False, reason="the spec review never approved the spec")

    with run.phase(PhaseParams(name="commit_plan", kind="code", owner="git",
                               description="Put the spec and its bar on record before any "
                                           "code")) as ph:
        commit(ph, plan)
    spec = next(a for a in plan.artifacts if Path(a).parts[:1] == ("specs",))

    def build_until_green(name: str, previous) -> tuple[BuildOutput, object]:
        with run.phase(PhaseParams(name=name, kind="agent", owner="builder", retries=1,
                                   description="Change the guard code as the spec says")) as ph:
            build = ph.call(AgentCall(output_type=BuildOutput, prompt=build_brief(spec),
                                      previous=previous, gates=[gates.diff_matches_claims]))
        test = None
        for j in range(1, MAX_FIX_LOOPS + 1):
            with run.phase(PhaseParams(name=f"{name}_verify_{j}", kind="code", owner="quality",
                                       description="Run the hub suite")) as ph:
                test = plan_checks.verify(run, plan)
                record(ph, test)
            if test.passed:
                break
            with run.phase(PhaseParams(name=f"{name}_fix_{j}", kind="agent", owner="builder",
                                       retries=1, description="Repair what the suite reported")) as ph:
                build = ph.call(AgentCall(output_type=BuildOutput, prompt=build_brief(spec),
                                          previous=quality.as_envelope(test, "checks"),
                                          gates=[gates.diff_matches_claims]))
        return build, test

    built, test = build_until_green("build", plan)
    heads: list[str] = []
    found_state: list[str] = []
    review: BypassReview | None = None
    accepted = False
    outcome = ""
    for i in range(1, MAX_ROUNDS + 1):
        if not test.passed:
            outcome = "the hub suite never passed"
            break
        head = snapshot(f"sssf({run.adw_id}): guard change, round {i}", cwd=cwd)
        with run.phase(PhaseParams(name=f"mechanisms_{i}", kind="code", owner="git",
                                   description="Look for new module-level state or globals in "
                                               "the guard modules")) as ph:
            since = heads[-1] if heads else base_sha
            state = mechanisms(since, head)
            ph.log(head=head[:12], since=since[:12], found=len(state),
                   state="; ".join(state) or "none")
        heads.append(head)
        if state and i > 1:
            found_state = state
            outcome = f"round {i - 1}'s fix added state to the guard modules"
            break
        found_state += state

        with run.phase(PhaseParams(name=f"bypass_review_{i}", kind="agent",
                                   owner="bypass_reviewer", retries=1,
                                   description="Hunt bypasses and harms against the spec's "
                                               "baseline and bar")) as ph:
            review = ph.call(AgentCall(
                output_type=BypassReview,
                prompt=review_brief(spec, base, base_sha, head, heads[-2] if i > 1 else None,
                                    i, test.tally()),
                gates=[gates.artifacts_exist, review_well_formed]))
            stops = blocking(review, allow_false_positives)
            ph.log(bypasses=len(review.bypasses), harms=len(review.harms),
                   false_positives=len(review.false_positives),
                   broken_promises=len(review.broken_promises),
                   old_holes=len(review.old_holes), adds_mechanism=review.adds_mechanism,
                   blocking=len(stops))

        if i > 1 and review.adds_mechanism:
            outcome = f"the reviewer found that round {i - 1}'s fix adds a mechanism"
            break
        if not stops:
            accepted = True
            outcome = f"round {i} found nothing that blocks"
            break
        if i == MAX_ROUNDS:
            outcome = f"{len(stops)} blocking finding(s) left after {MAX_ROUNDS} rounds"
            break
        _, test = build_until_green(f"fix_round_{i}", review)

    with run.phase(PhaseParams(name="report", kind="code", owner="git",
                               description="Write every finding down for the engineer")) as ph:
        report_path = run.context_handoff_dir / "guard_report.md"
        write_report(report_path, review, outcome, heads, found_state)
        ph.log(outcome=outcome, report=str(report_path))

    if accepted:
        with run.phase(PhaseParams(name="commit_build", kind="code", owner="git",
                                   description="Land the code: green suite, nothing blocking")) as ph:
            commit(ph, built)

    return run.finish(accepted=accepted, reason=outcome)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    parser.add_argument("--worktree", action="store_true",
                        help="work on branch sssf/<adw_id> in its own worktree")
    parser.add_argument("--base", default="main", help="the baseline ref, main by default")
    parser.add_argument("--allow-false-positives", action="store_true",
                        help="turn the no_false_positive stop off for this run")
    args = parser.parse_args()
    sys.exit(main(utils.resolve_prompt(args.prompt), args.config, args.adw_id, args.worktree,
                  args.base, args.allow_false_positives))
