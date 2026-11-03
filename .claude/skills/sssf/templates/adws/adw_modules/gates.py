"""Validation gates: verify the envelope's CLAIMS, never guesses.

A gate is `gate(envelope, run) -> GateReport` — one check per item it looked at.
Violations are derived from the failed checks and sent back to the SAME agent
session as a correction. Every check is recorded either way, so a green gate
says WHAT it verified instead of only that it passed.

Gates check what is mechanically checkable; plan quality is a reviewer's job.
`plan_well_formed` checks a plan's form, not whether it's a good plan.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import sandbox
from .data_types import EnvelopeBase, GateReport

TAIL_CHARS = 1000        # command output kept as evidence on a failure


def _size(path: Path) -> str:
    n = sandbox.current().size(path)
    return f"{n}B" if n < 1024 else f"{n / 1024:.1f}KB"


def artifacts_exist(envelope: EnvelopeBase, run) -> GateReport:
    report = GateReport()
    for a in envelope.artifacts:
        p = Path(a)
        found = sandbox.current().exists(p)
        report.check(a, found, f"exists, {_size(p)}" if found else "declared artifact does not exist")
    return report


def files_non_empty(envelope: EnvelopeBase, run) -> GateReport:
    report = GateReport()
    for a in envelope.artifacts:
        p = Path(a)
        if not sandbox.current().is_file(p):
            continue                       # existence is artifacts_exist's job
        empty = sandbox.current().size(p) == 0
        report.check(a, not empty, "declared artifact is empty" if empty else _size(p))
    return report


def json_parses(envelope: EnvelopeBase, run) -> GateReport:
    report = GateReport()
    for a in envelope.artifacts:
        p = Path(a)
        if p.suffix != ".json" or not sandbox.current().exists(p):
            continue
        try:
            parsed = json.loads(sandbox.current().read_text(p))
            report.check(a, True, f"parses, {type(parsed).__name__}")
        except json.JSONDecodeError as e:
            report.check(a, False, f"declared JSON artifact does not parse: {e}")
    return report


def diff_matches_claims(envelope: EnvelopeBase, run) -> GateReport:
    """Every file claimed changed must exist on disk."""
    report = GateReport()
    for f in getattr(envelope, "changed_files", []):
        p = Path(f)
        found = sandbox.current().exists(p)
        report.check(f, found, f"exists, {_size(p)}" if found else "claimed changed file does not exist")
    return report


# A heading on how to prove the change works: "Verification", "Testing",
# "How to verify", "Acceptance criteria", "Checks".
VERIFY_HEADING = re.compile(r"^#+ .*(verif|validat|test|check|accept)", re.IGNORECASE | re.MULTILINE)
# A backticked token that looks like a path: `src/app.py`, `src/app.py:12`, `src/`.
PATH_TOKEN = re.compile(r"`([^`\s]*[/.][^`\s]*)`")


def _names_a_real_file(text: str) -> str:
    """The first backticked path that exists, or that would go in a folder
    that exists. A new file at the root doesn't count on its own: `v1.2` and
    `newthing.py` look the same."""
    for token in PATH_TOKEN.findall(text):
        path = Path(token.split(":")[0].rstrip("/") or ".")
        if str(path) in (".", "..") or path.is_absolute():
            continue
        if sandbox.current().exists(path):
            return token
        if str(path.parent) != "." and sandbox.current().exists(path.parent):
            return token
    return ""


def plan_well_formed(envelope: EnvelopeBase, run) -> GateReport:
    """What the planner's prompt asks for, as far as code can see: the
    builder's copy and the spec, the same text, a spec that overwrites no
    earlier one, a way to verify, and a real file named. Each failure goes
    back to the planner, so a malformed plan costs one turn, not a build."""
    report = GateReport()
    handoff = [a for a in envelope.artifacts if Path(a).name == "plan.md"]
    specs = [a for a in envelope.artifacts if Path(a).parts[:1] == ("specs",)]
    if not handoff or not specs:
        missing = "plan.md in context_handoff_dir" if not handoff else "no copy under specs/"
        return report.check("copies", False, f"declare both copies in artifacts: {missing}")
    box = sandbox.current()
    if not (box.is_file(Path(handoff[0])) and box.is_file(Path(specs[0]))):
        return report.check("copies", False, "a declared copy is missing; artifacts_exist names it")
    text = box.read_text(Path(handoff[0]))
    same = box.read_text(Path(specs[0])) == text
    report.check("copies", same, "plan.md and the spec match" if same else
                 f"{specs[0]} differs from plan.md: copy the file, don't retype it")
    tracked = box.run(["git", "ls-files", "--error-unmatch", "--", specs[0]],
                      cwd=getattr(run, "repo_root", None)).returncode == 0
    report.check("spec", not tracked, "a new spec" if not tracked else
                 f"{specs[0]} is an earlier, committed spec; restore it with git checkout and "
                 "use the next free name, _v2, _v3")
    verify = VERIFY_HEADING.search(text)
    report.check("verify", bool(verify), f"has {verify.group(0).strip()!r}" if verify else
                 "no section on how to verify: add a heading such as '## Verification' with the "
                 "commands that prove the change works")
    named = _names_a_real_file(text)
    report.check("files", bool(named), f"names {named}" if named else
                 "names no file or folder that exists in the repo: name each file to touch, "
                 "in backticks, as a path from the repo root")
    return report


def verdict_consistent(envelope: EnvelopeBase, run) -> GateReport:
    """A review's verdict must agree with the findings it just wrote down.

    Nothing here judges the code — that is the reviewer's job. This checks the
    envelope against itself: an approval that ships blocking items, or a
    rejection that names no problem, is a claim the harness can refute without
    reading a line of the diff.
    """
    report = GateReport()
    approved = bool(getattr(envelope, "approved", False))
    blocking = list(getattr(envelope, "blocking", []))
    unmet = [f.requirement for f in getattr(envelope, "findings", []) if not f.met]

    report.check("approved vs blocking", not (approved and blocking),
                 "no blocking items" if not blocking
                 else f"{len(blocking)} blocking item(s) while approved=true"
                 if approved else f"{len(blocking)} blocking item(s), not approved")
    report.check("approved vs findings", not (approved and unmet),
                 "every requirement met" if not unmet
                 else f"{len(unmet)} unmet requirement(s) while approved=true"
                 if approved else f"{len(unmet)} unmet requirement(s), not approved")
    report.check("rejection names a problem", approved or bool(blocking or unmet),
                 "verdict is supported" if approved or blocking or unmet
                 else "approved=false but no blocking item or unmet requirement was given")
    return report


def panel_complete(labels: list[str]):
    """A gate for a trial panel reviewer: a verdict on every attempt it was
    shown, each one agreeing with its own findings, and a ranking of them all."""

    def gate(envelope: EnvelopeBase, run) -> GateReport:
        report = GateReport()
        verdicts = {v.label: v for v in getattr(envelope, "verdicts", [])}
        for label in labels:
            verdict = verdicts.get(label)
            report.check(f"verdict on {label}", verdict is not None,
                         "given" if verdict else "no verdict for this attempt")
            if verdict is None:
                continue
            unmet = [f for f in verdict.findings if not f.met]
            report.check(f"{label}: approved vs blocking and findings",
                         not (verdict.approved and (verdict.blocking or unmet)),
                         "agrees" if not verdict.approved or not (verdict.blocking or unmet)
                         else "approved with blocking items or unmet requirements")
            report.check(f"{label}: a block names a problem",
                         verdict.approved or bool(verdict.blocking or unmet),
                         "agrees" if verdict.approved or verdict.blocking or unmet
                         else "approved=false but no blocking item or unmet requirement was given")
        extra = sorted(set(verdicts) - set(labels))
        report.check("no verdict on an unknown label", not extra,
                     "none" if not extra else f"unknown: {', '.join(extra)}")
        ranking = list(getattr(envelope, "ranking", []))
        report.check("ranking lists every attempt once", sorted(ranking) == sorted(labels),
                     " > ".join(ranking) if sorted(ranking) == sorted(labels)
                     else f"got {ranking}, expected each of {labels} once")
        return report

    gate.__name__ = "panel_complete"
    return gate


# Every planner call takes these.
PLAN = [artifacts_exist, files_non_empty, plan_well_formed]


def tests_pass(command: str):
    """Gate factory: the given shell command must exit 0."""
    def gate(envelope: EnvelopeBase, run) -> GateReport:
        result = sandbox.current().run(command, shell=True)
        ok = result.returncode == 0
        note = f"exit {result.returncode}"
        if not ok:
            note += "\n" + (result.stdout + result.stderr)[-TAIL_CHARS:]
        return GateReport().check(command, ok, note)
    gate.__name__ = f"tests_pass({command})"
    return gate
