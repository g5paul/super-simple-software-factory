#!/usr/bin/env -S uv run
# /// script
# dependencies = ["pydantic", "python-dotenv", "pyyaml", "rich"]
# ///
"""ADW Plan Build Test — the full starter chain.

Usage:
    uv run adws/adw_plan_build_test.py "<prompt or path/to/prompt.md>" [--config adws/adw_sssf_config/sssf.config.yaml] [--adw-id a1b2c3d4] [--worktree] [--sandbox vercel|e2b|none]

Phases: engineer(request) -> planner -> builder -> code(test) [-> builder(fix) -> code(test) ... bounded] -> git(commit)

Testing is CODE: the suite's command lives in adw_modules/quality.py, so no
agent spends a context window rediscovering it. Failures flow back to the
builder as an envelope, and only an exhausted fix loop fails the run.
"""

import argparse
import sys

from adw_modules import agents, gates, git_helper, plan_checks, quality, session, utils
from adw_modules.data_types import AgentCall, BuildOutput, PhaseParams, PlanOutput

REQUIRED_AGENTS = ["planner", "builder"]
MAX_FIX_LOOPS = 3


def main(prompt: str, config: str = "adws/adw_sssf_config/sssf.config.yaml", adw_id: str | None = None,
         worktree: bool = False, sandbox_name: str | None = None) -> int:
    cfg = agents.load_config(config)
    agents.validate(cfg, REQUIRED_AGENTS)
    run = session.ensure(cfg, adw_id, use_worktree=worktree, sandbox_name=sandbox_name,
                         repo_sandbox=True)

    def record(ph, result) -> None:
        ph.log(passed=result.passed, checks=result.tally(),
               artifacts=", ".join(result.artifacts))

    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="Capture the incoming ask")) as ph:
        ph.log(input=prompt)

    with run.phase(PhaseParams(name="plan", kind="agent", owner="planner",
                               description="Turn the request into an implementable plan")) as ph:
        plan = ph.call(AgentCall(output_type=PlanOutput, prompt=prompt,
                                 gates=gates.PLAN))

    with run.phase(PhaseParams(name="build", kind="agent", owner="builder",
                               description="Implement the plan exactly")) as ph:
        previous = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt, previous=plan,
                                     gates=[gates.artifacts_exist]))
    # The first build describes the whole change. A fix or a revision reports
    # only its own repair, so its message must never name the code commit.
    built = previous

    test = None
    for i in range(1, MAX_FIX_LOOPS + 1):
        with run.phase(PhaseParams(name=f"test_{i}", kind="code", owner="quality",
                                   description="Run the suite — a known command, so code runs "
                                               "it and no agent has to rediscover it")) as ph:
            test = plan_checks.verify(run, plan, base=quality.run_tests)
            record(ph, test)

        if test.passed:
            break
        if i == MAX_FIX_LOOPS:
            break                           # a fix after the last test is never checked

        with run.phase(PhaseParams(name=f"fix_{i}", kind="agent", owner="builder", retries=1,
                                   description="Repair what the suite reported, from its "
                                               "verbatim output")) as ph:
            previous = ph.call(AgentCall(output_type=BuildOutput, prompt=prompt,
                                         previous=quality.as_envelope(test, "tests"),
                                         gates=[gates.artifacts_exist]))

    # Only tested work gets committed — a red suite leaves the tree uncommitted.
    if test is not None and test.passed:
        with run.phase(PhaseParams(name="commit", kind="code", owner="git",
                                   description="Land the code only after the suite came back green")) as ph:
            message = built.commit_message or f"sssf({run.adw_id}): {built.summary}"
            ph.log(sha=git_helper.commit_all(message), message=message)

    return run.finish(accepted=test is not None and test.passed,
                      reason=f"the suite still failed after {MAX_FIX_LOOPS - 1} fix attempt(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="inline text or a path to a prompt file")
    parser.add_argument("--config", default="adws/adw_sssf_config/sssf.config.yaml")
    parser.add_argument("--adw-id", default=None, help="join or pin an existing session")
    parser.add_argument("--worktree", action="store_true",
                        help="work on branch sssf/<adw_id> in its own worktree; `just merge <adw_id>` lands it")
    parser.add_argument("--sandbox", choices=["vercel", "e2b", "none"], default=None,
                        help="do the run's work in a remote VM, which implies --worktree, or on "
                             "this machine with none; repo.yaml's `sandbox` when left out")
    args = parser.parse_args()
    sys.exit(main(utils.resolve_prompt(args.prompt), args.config, args.adw_id, args.worktree,
                  args.sandbox))
