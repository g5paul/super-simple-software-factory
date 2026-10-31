#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Tickets — build an approved feature one ticket at a time, test-first.

Usage:
    uv run adws/adw_tickets.py .scratch/<feature>/ [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4] [--worktree]

The engineer's half runs first, interactively, with mattpocock/skills:
`grill-with-docs`, then `to-spec`, then `to-tickets` on the local markdown
tracker. They leave `.scratch/<feature>/spec.md` and one `issues/NN-<slug>.md`
per ticket, committed. This ADW starts from that folder and runs headless.

Phases: engineer(request) -> code(read) -> scout
        -> per ticket, blockers first:
           ticket_builder(tNN_build) -> code(tNN_verify_1)
           [-> ticket_builder(tNN_fix_1) -> code(tNN_verify_2) ... bounded]
           -> git(tNN_commit)
        -> standards_reviewer(review_standards) -> spec_reviewer(review_spec)
        [-> ticket_builder(review_fix) -> code(review_verify_1) ... -> git(commit_review_fix)]
        -> git(changes) -> documenter -> git(commit_docs)

The read step is code. It rejects a bad folder (no spec seams, a missing
blocker, a cycle) before any agent spends money, and prints what the feature
has cost so far and an estimate for the rest.

Every ticket gets a fresh builder session: the spec, its ticket, the scout's
notes, and the commits so far are all it needs, and a clean context costs
less than one that carries every earlier ticket. Fixes for a ticket stay in
that ticket's session.

A ticket lands as its own commit, "NN: title", only after every quality.py
block passes, and that commit also marks the ticket done. A rerun skips done
tickets, so it resumes where the last run stopped. Every commit carries an
`SSSF-Feature` trailer; the first one marks where the feature's diff starts,
and the docs commit's `SSSF-Step: documented` marks it finished.

The two reviewers ask separate questions, blind to each other: does the code
follow this repo's standards, and does it do what the spec asked? One fix
pass follows, only for blocking findings, then the checks again.

A stop never discards work. A ticket that still fails, a cost cap, or a usage
window leaves earlier tickets committed and says how to look at, keep, or drop
whatever is uncommitted.

With --worktree the whole feature is built on branch sssf/<adw_id>, in its own
worktree, and may launch from main; `just merge <adw_id>` lands it. A stopped
run resumes by rejoining with --adw-id, which starts every agent fresh and
reuses the scout's notes. While a worktree run holds a feature folder, no
other run builds it. Design: docs/designs/parallel-worktree-runs.md in the SSSF
repo, decisions 26 to 38.
"""

import argparse
import os
import sys
from pathlib import Path

from adw_modules import (account, agents, budget, changes, gates, git_helper, permissions, quality,
                         session, tickets, worktree)
from adw_modules.data_types import (AgentCall, AxisReviewOutput, BuildOutput, ChangeCapture,
                                    DocumentOutput, EventRecord, GenericOutput, PhaseParams,
                                    ScoutOutput)
from adw_modules.utils import just

REQUIRED_AGENTS = ["scout", "ticket_builder", "standards_reviewer", "spec_reviewer", "documenter"]
MAX_FIX_LOOPS = 3
PROTECTED_BRANCHES = ("main", "master")

DOCUMENT_NOTES = ("Read diff_path in full before writing. Document only what the "
                  "diff shows: the whole feature, every ticket. Then copy the "
                  "write-up into app_docs/ as your task describes.")


def main(folder: str, config: str = "adws/adw_sssf_config/sssf.config.yaml", adw_id: str | None = None,
         use_worktree: bool = False) -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)
    claim(cfg, folder, adw_id, use_worktree)
    run = session.ensure(cfg, adw_id, use_worktree=use_worktree)
    # Every attempt starts its agents fresh, so a rejoin's builder doesn't
    # carry the attempt that failed. Within an attempt, fixes keep their session.
    run.agent_map = {}
    folder = os.path.relpath(Path(folder).resolve(), run.repo_root)
    try:
        code = build_feature(run, folder)
        if worktree.recorded(run.cfg.observability.db, run.adw_id):
            run.console.note(done_marks(run))
        return code
    except Exception as error:
        stopped(run, folder, error)
        if isinstance(error, (budget.BudgetStop, account.RateLimitStop, permissions.PermissionBreach)):
            return 1                       # a stop, not a crash: the message says it all
        raise


def build_feature(run, folder: str) -> int:
    def commit(ph, subject: str, body: str = "", step: str = "") -> None:
        trailers = f"SSSF-Feature: {feature.slug}" + (f"\nSSSF-Step: {step}" if step else "")
        message = "\n\n".join(part for part in (subject, body, trailers) if part)
        ph.log(sha=git_helper.commit_all(message), message=subject)

    def record(ph, result) -> None:
        ph.log(passed=result.passed, checks=result.tally(),
               artifacts=", ".join(result.artifacts))

    def verify(prefix: str, owner_session: str, prompt: str):
        """Run every check; on red, the same session fixes, bounded. Returns the last result."""
        result = None
        for i in range(1, MAX_FIX_LOOPS + 1):
            with run.phase(PhaseParams(name=f"{prefix}_verify_{i}", kind="code", owner="quality",
                                       description="Run the suite and every quality check")) as ph:
                result = quality.run_quality(run)
                record(ph, result)
            if result.passed or i == MAX_FIX_LOOPS:
                return result
            with run.phase(PhaseParams(name=f"{prefix}_fix_{i}", kind="agent", owner="ticket_builder",
                                       retries=1, description="Repair what the checks reported, "
                                                              "from their verbatim output")) as ph:
                ph.call(AgentCall(output_type=BuildOutput, prompt=prompt, session=owner_session,
                                  previous=quality.as_envelope(result, "checks"),
                                  gates=[gates.diff_matches_claims]))
        return result

    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Capture the feature folder to build")) as ph:
        ph.log(input=folder)

    notes = run.context_handoff_dir / "scout_findings.md"
    with run.phase(PhaseParams(name="read", kind="code", owner="tickets",
                               description="Check the feature folder and order its tickets, "
                                           "before any agent spends money")) as ph:
        feature = tickets.load(folder)
        preflight(run, feature)
        history = git_helper.feature_commits(feature.slug)
        base = git_helper.rev(f"{history[0][0]}^") if history else git_helper.rev("HEAD")
        finished = any(step == "documented" for _, step in history)
        # For the dashboard, which groups this run's phases by ticket. Not a
        # console line: the log line below already says what will be built.
        run.tracer.event(EventRecord(
            adw_id=run.adw_id, phase_id=ph.phase.phase_id, type="log", name="tickets",
            payload={"feature": feature.slug, "folder": folder,
                     "tickets": [{"number": t.number, "title": t.title, "path": t.path,
                                  "done": t.done} for t in feature.tickets]}))
        ph.log(feature=feature.slug, tickets=len(feature.tickets),
               to_build=" ".join(t.number for t in feature.todo) or "none",
               base=git_helper.short_sha(base),
               spent_so_far=f"${tickets.spent_on(run.cfg.observability.db, folder):.2f}",
               estimate=tickets.estimate(run.cfg.observability.db, len(feature.todo)))
        if feature.todo and notes.is_file():
            ph.log(scout=f"skipped, reusing {notes.name} from an earlier attempt")
    if finished:
        run.console.note(f"{feature.slug} is already built and documented; nothing to do.")
        return run.finish()

    if feature.todo and not notes.is_file():
        with run.phase(PhaseParams(name="scout", kind="agent", owner="scout",
                                   description="Explore once for the whole feature, so no "
                                               "builder has to find its own way")) as ph:
            ph.call(AgentCall(output_type=ScoutOutput, prompt=scout_prompt(feature, notes),
                              gates=[gates.artifacts_exist]))

    for ticket in feature.todo:
        n = f"t{ticket.number}"
        prompt = ticket_prompt(feature, ticket, base, notes)
        with run.phase(PhaseParams(name=f"{n}_build", kind="agent", owner="ticket_builder", retries=1,
                                   description=f"Build ticket {ticket.number}, test-first: "
                                               f"{ticket.title}")) as ph:
            build = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt, session=n,
                                      gates=[gates.diff_matches_claims]))
        if not verify(n, n, prompt).passed:
            return give_up(run, folder, f"ticket {ticket.number} ({ticket.title}) still failed its "
                                        f"checks after {MAX_FIX_LOOPS - 1} fixes. Earlier tickets "
                                        f"are committed; rerun to resume at this one.")
        with run.phase(PhaseParams(name=f"{n}_commit", kind="code", owner="git",
                                   description=f"Mark ticket {ticket.number} done and land it "
                                               f"on its own")) as ph:
            tickets.mark_done(ticket)
            commit(ph, f"{ticket.number}: {ticket.title}", build.commit_message)

    # SSSF's own files are not the feature's work. Updating SSSF on the feature
    # branch between runs is routine, and a reviewer shown it calls it scope creep.
    outside = " ".join(f"':(exclude){p}'" for p in git_helper.SSSF_PATHS)
    diff = f"git diff {git_helper.short_sha(base)}..HEAD -- . {outside}"
    commits = git_helper.log_oneline(base, exclude=git_helper.SSSF_PATHS)
    protected = ", ".join(run.cfg.defaults.protected_files)
    hands_off = (f"Protected paths, which no agent may change: {protected}. A finding that needs "
                 f"one of them changed is for the engineer, so it is never blocking.")
    with run.phase(PhaseParams(name="review_standards", kind="agent", owner="standards_reviewer",
                               description="Does the feature follow this repo's standards?")) as ph:
        standards = ph.call(AgentCall(output_type=AxisReviewOutput,
                                      prompt=f"Diff: {diff}\nCommits:\n{commits}\n{hands_off}",
                                      gates=[gates.artifacts_exist, gates.files_non_empty]))
    with run.phase(PhaseParams(name="review_spec", kind="agent", owner="spec_reviewer",
                               description="Does the feature do what its spec and tickets ask?")) as ph:
        spec = ph.call(AgentCall(output_type=AxisReviewOutput,
                                 prompt=(f"Spec: {feature.spec}\nTickets:\n"
                                         + "\n".join(t.path for t in feature.tickets)
                                         + f"\nDiff: {diff}\nCommits:\n{commits}\n{hands_off}"),
                                 gates=[gates.artifacts_exist, gates.files_non_empty]))

    # A reviewer can still block on a file the fixer may not touch. Such a
    # finding would only trip the permission check and lose the whole fix pass,
    # so it goes to the engineer instead.
    builder = agents.resolve(run.cfg, "ticket_builder")
    blocking, for_engineer = [], []
    for f in standards.blocking + spec.blocking:
        path = f.where.split(":")[0].strip()
        fixable = not path or permissions.permitted(path, builder, run.cfg)
        (blocking if fixable else for_engineer).append(f)
    if for_engineer:
        run.console.advise("blocking findings for the engineer, in files no agent may change:\n"
                           + "\n".join(f"- {f.where}: {f.problem}" for f in for_engineer))
    if blocking:
        prompt = (f"Fix the blocking review findings for the feature in {folder}. "
                  f"Spec: {feature.spec}. The reviews are in previous_envelope.artifacts. "
                  f"Never change these protected paths: {protected}. A finding that needs one "
                  f"of them changed is for the engineer: skip it, and say so in notes_for_next_agent.")
        findings = GenericOutput(
            status="success", summary=f"{len(blocking)} blocking finding(s)",
            artifacts=standards.artifacts + spec.artifacts,
            notes_for_next_agent="\n".join(
                f"- {'BLOCKING' if f.blocking else 'judgement'}: {f.where}: {f.problem} ({f.source})"
                for f in standards.findings + spec.findings))
        with run.phase(PhaseParams(name="review_fix", kind="agent", owner="ticket_builder", retries=1,
                                   description="One fix pass for what the reviewers blocked on")) as ph:
            fix = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt, session="review_fix",
                                    previous=findings, gates=[gates.diff_matches_claims]))
        if not verify("review", "review_fix", prompt).passed:
            return give_up(run, folder, "the review fix still failed its checks. Every ticket is "
                                        "committed; rerun to review again.")
        if git_helper.work_in_progress(run.cfg.defaults.data_dir):
            with run.phase(PhaseParams(name="commit_review_fix", kind="code", owner="git",
                                       description="Land the review fix on its own")) as ph:
                commit(ph, fix.commit_message or f"Fix review findings for {feature.slug}",
                       step="review-fix")

    with run.phase(PhaseParams(name="changes", kind="code", owner="git",
                               description="Diff the whole feature, for the documenter")) as ph:
        changeset = changes.capture(run, ChangeCapture(base=base))
        ph.log(base=f"{changeset.base.label} @ {changeset.base.commit[:7]}",
               files=len(changeset.files) + len(changeset.untracked),
               lines=f"+{changeset.insertions} -{changeset.deletions}",
               diff=changeset.diff_path)

    with run.phase(PhaseParams(name="document", kind="agent", owner="documenter", retries=1,
                               description="Write up the finished feature")) as ph:
        document = ph.call(AgentCall(output_type=DocumentOutput,
                                     prompt=f"The feature in {folder}, specified in {feature.spec}.",
                                     previous=changes.as_envelope(changeset, DOCUMENT_NOTES),
                                     gates=[gates.artifacts_exist, gates.files_non_empty]))

    with run.phase(PhaseParams(name="commit_docs", kind="code", owner="git",
                               description="Ship the write-up, and mark the feature finished")) as ph:
        commit(ph, document.commit_message or f"Document {feature.slug}", step="documented")

    return run.finish()


def claim(cfg, folder: str, adw_id: str | None, use_worktree: bool) -> None:
    """Before any worktree or agent: one branch per feature at a time, and a new
    worktree run builds from the feature folder as committed."""
    folder = os.path.relpath(Path(folder).resolve(), git_helper.repo_root())
    holder = tickets.held_by(cfg.observability.db, folder, exclude=adw_id or "")
    if holder:
        raise SystemExit(
            f"FeatureError: run {holder} is building {folder} on its own branch, sssf/{holder}. "
            f"Resume it with `{just(f'tickets {folder} --adw-id {holder}')}`, or drop it with "
            f"`{just(f'discard {holder}')}` first.")
    if not use_worktree or (adw_id and worktree.recorded(cfg.observability.db, adw_id)):
        return
    edited = git_helper.work_in_progress(cfg.defaults.data_dir)
    edited = [line for line in edited if line.split(maxsplit=1)[-1].startswith(folder.rstrip("/") + "/")]
    if edited:
        raise SystemExit(
            f"FeatureError: {folder} has uncommitted changes, and a worktree run builds from what is "
            f"committed. Commit them first:\n" + "\n".join(edited[:10]))


def done_marks(run) -> str:
    return (f"done marks are on sssf/{run.adw_id} until `{just(f'merge {run.adw_id}')}`; "
            f"the feature folder in your checkout shows them after the merge.")


def preflight(run, feature) -> None:
    """The guards that keep a headless run's commits where the engineer expects them."""
    branch = git_helper.current_branch()
    if branch in PROTECTED_BRANCHES:
        raise tickets.FeatureError(f"refusing to build on {branch}. Make a branch for the feature first.")
    if not git_helper.tracked(feature.spec):
        hint = ("It is gitignored: remove the ignore rule, or force-add it with `git add -f`, "
                "then commit it." if git_helper.ignored(feature.spec) else "Commit it first.")
        raise tickets.FeatureError(
            f"{feature.folder} is not committed. adw_tickets commits each ticket's done "
            f"status beside the code, so the feature folder must be tracked. {hint}")
    dirty = git_helper.work_in_progress(run.cfg.defaults.data_dir)
    if dirty:
        raise tickets.FeatureError(
            "the tree has uncommitted changes. Commit the feature folder, and keep, stash, or "
            "drop anything else, then rerun:\n" + "\n".join(dirty[:10]))


def scout_prompt(feature, notes: Path) -> str:
    listed = "\n".join(f"- {t.path} ({t.number}: {t.title})" for t in feature.todo)
    return (f"Explore this codebase for a feature that is about to be built, one ticket at a "
            f"time. Change nothing.\n\nSpec: {feature.spec}\nTickets, in build order:\n{listed}\n\n"
            f"Write {notes} with: where the code these tickets touch lives, the patterns and "
            f"test setup to follow, and for each ticket the files it will likely change. Keep it "
            f"under 300 lines; every builder reads it.")


def ticket_prompt(feature, ticket, base: str, notes: Path) -> str:
    done = git_helper.log_oneline(base)
    return (f"Build ticket {ticket.number} of the feature in {feature.folder}.\n\n"
            f"Spec: {feature.spec} (the seams are under \"Testing Decisions\")\n"
            f"Your ticket: {ticket.path}\n"
            f"Scout's notes: {notes}\n"
            f"Committed so far on this feature:\n{done or '(nothing yet: yours is the first ticket)'}")


def give_up(run, folder: str, reason: str) -> int:
    """A ticket or the review fix stayed red: finish the run, then say what is left."""
    code = run.finish(accepted=False, reason=reason)
    stopped(run, folder, reason)
    return code


def stopped(run, folder: str, error: Exception | str) -> None:
    """Say why the run stopped, what the feature has cost, and what is left in the tree."""
    try:
        spent = tickets.spent_on(run.cfg.observability.db, folder)
    except Exception:
        spent = run.cost
    message = f"stopped: {error}\nthis feature has cost ${spent:.2f} so far, across every run."
    if worktree.recorded(run.cfg.observability.db, run.adw_id):
        message += (f"\nresume it with `{just(f'tickets {folder} --adw-id {run.adw_id}')}`. "
                    + done_marks(run))
    if "git stash" not in str(error):
        message += budget.uncommitted_help(run)
    run.console.advise(message)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", help="the feature folder: .scratch/<feature>/")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    parser.add_argument("--worktree", action="store_true",
                        help="build on branch sssf/<adw_id> in its own worktree; `just merge <adw_id>` lands it")
    args = parser.parse_args()
    sys.exit(main(args.folder, args.config, args.adw_id, args.worktree))
