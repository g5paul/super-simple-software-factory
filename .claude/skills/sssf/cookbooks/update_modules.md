# Update Modules

Extend `adws/adw_modules/` with new low-level logic.

## The rule

**ALL low-level logic lives in `adw_modules/`; ADW scripts stay thin.** An `adw_*.py` file declares agents, sequences phases, and returns an exit code. Anything else — subprocess handling, parsing, retry mechanics, git plumbing, reusable predicates — goes in a module.

## Where things go

| Module | Owns |
|---|---|
| `data_types.py` | Every Pydantic model: `AgentCall`, `PhaseParams`, `Phase`, `EnvelopeBase` + one output type per agent call, the config models (`AgentConfig`, `SSSFConfig`), `EventRecord`, and `AgentRequest`/`AgentResult` |
| `agents.py` | `load_config`, `validate`, resolving an entry → coding-agent interface + model + thinking + harness extensions |
| `runner.py` | the `Run` object; `run.phase(PhaseParams)` context manager; `ph.call(AgentCall)` |
| `agent_pi.py` | the Pi interface (v1) — non-interactive `pi -p --mode json`, JSONL stream tailed live, model resolved against `~/.pi/agent/models.json`; `--session-id` creates-or-continues, so running and continuing an agent are the same call |
| `agent_cc.py` | the Claude Code interface. `claude -p --output-format stream-json`, same contract as `agent_pi.run()`. `--session-id` creates and `--resume` continues, tracked by a per-session ledger that also turns Claude's running cost total into per-send cost |
| `gates.py` | validation gates over envelope claims |
| `changes.py` | deterministic change capture: resolve the base ref, `git diff` into `context_handoff/changes.diff`, adapt the `ChangeSet` into an envelope an agent can be handed |
| `prompts.py` | load system/user prompt refs from config, render placeholders |
| `session.py` | mint or join `adw_id`, maintain `agent_map.json`, create session dirs incl. `context_handoff/` |
| `tracer.py` | append JSONL **and** insert every event into `sssf.db` as it happens |
| `console.py` | the terminal narrative — every line printed also lands in the db as a `log` event, so the UI reads the same story; plain sequential lines, no spinners |
| `git_helper.py` | branch, status, diff, commit — the raw plumbing `changes.py` composes |
| `utils.py` | safe subprocess env, logging, `resolve_prompt` |
| `plan_checks.py` | the plan's own checks, such as a validation script the plan requires, run as code beside `quality.py`'s blocks |
| `quality.py` | the repo's fixed commands: test, lint, format, typecheck, build. The one module a repo edits, and `--update` keeps it |
| `permissions.py` | what an agent may change: checks `writes:` and `protected_files` after every agent call and rolls back what it wasn't allowed to change |
| `rules.py` | the repo's `agent_rules` from `repo.yaml`, checked against every shell command an agent runs |
| `sssf_refuse.py` | refuse mode for `agent_rules` and the secret-file rule on Claude Code agents: refuses a matching command before it runs |
| `sssf_rules.ts` | the same refuse mode as a pi extension, loaded into every pi agent; also blocks `read`, `grep`, `find` and `ls` on a secret file |
| `secret_files.py` | the files agents must not read: `.env` and its kin, plus `repo.yaml`'s `secret_files` |
| `repo_settings.py` | loads `adws/adw_sssf_config/repo.yaml`, the repo's rules that no roster swap can change |
| `budget.py` | cost caps that stop spending before an unattended run gets expensive |
| `account.py` | the Claude account's 5-hour and 7-day usage windows, as Claude Code last reported them |
| `prices.py` | Claude's list prices from `prices.json`, for a Claude Code call killed before it reported its own cost |
| `silence.py` | stops an agent that writes nothing for `repo.yaml`'s `max_silence_minutes` |
| `kill.py` | stops a run by `adw_id`: the chain first, then its coding agents, found from the trace's `processes` |
| `worktree.py` | a run on its own `sssf/<adw_id>` branch in its own worktree, with `--worktree` |
| `sandbox.py` | where a run's tree lives; every process and file read SSSF does in it goes through `current()` |
| `sandbox_remote.py` | a run's tree in a remote VM, with a clone of the run's branch at the host worktree's path |
| `sandbox_vercel.py`, `sandbox_e2b.py` | make, refresh and delete a Vercel Sandbox or E2B VM for `sandbox_remote.py` |
| `sandbox_usage.py` | what remote VM runs have used of each provider's free allowance, from `sandbox_end` events |
| `obs_relay.py`, `sssf_obs_relay.cjs` | the host and VM sides of the relay that carries a pi agent's obs events out of a VM |
| `tickets.py` | reads a feature folder written by `to-spec` and `to-tickets` for `adw_tickets.py` |
| `trial.py` | trials: one request, several rosters, a panel's verdict, the best one merged |

Guard code is SSSF's limit on agents: `rules.py`, `sssf_refuse.py`, `sssf_rules.ts`, `secret_files.py`, `permissions.py`, `agent_cc.py`, `agent_pi.py` and `gates.py`. A change to any of them goes through the SSSF hub's guard-change chain, not a local edit.

## Never `print()`

Modules report through `run.console` — never a bare `print()`. Each console method prints a rich line **and** writes it to `sssf.db` as a `log` event with payload `{message, level}`, both from one `_emit` helper, so the terminal narrative and the swim-lane UI can't drift. New output means a new method on `Console`, not a print at the call site.

## The four-param rule

**Any function taking more than 4 parameters gets them converted into a concrete data type in `data_types.py`.** `AgentCall` and `PhaseParams` are the pattern — `run.phase()` and `ph.call()` each take exactly one object. This is skill-wide: every module the factory generates obeys it.

```python
class ReviewParams(BaseModel):
    """Everything review_changes() needs. Passed as one object, never loose params."""
    base_ref: str
    paths: list[str]
    max_diff_lines: int = 2000
    ignore_generated: bool = True
    reviewer: str = "scout"
```

## Adding an output type

Every agent call parses against a concrete type. Extend `EnvelopeBase` — `status`, `summary`, `artifacts`, `notes_for_next_agent` — with only the fields that call actually needs:

```python
class ReviewOutput(EnvelopeBase):
    approved: bool
    blocking: list[str] = []
```

**The output contract is a synced triad — one change means three edits, always together:**

1. The type in `data_types.py` (the enforcer).
2. The agent's `user.md` `## Report` section showing exactly that JSON (the ask).
3. Every call site passing `output_type=` (the binding) — `grep -rn "ReviewOutput" adws/` to find them all.

If the type and the Report example drift, the agent produces what the prompt asked for, the parser rejects what the type expects, and every call burns correction round-trips before landing — a slow, silent tax. Renaming or removing a field is the same triad edit. Schema details: `references/handoff.md`.

## Adding a gate

A gate is a callable — `gate(envelope, run) -> GateReport`. You record **one check per item you look at**, and the harness derives the verdict: any failed check is a violation, and no failed checks means pass.

```python
from adw_modules.data_types import GateReport

def tests_declared_passed(envelope, run) -> GateReport:
    """Verify the envelope's own test claims, after the fact."""
    report = GateReport()
    for f in envelope.failures:
        report.check(f.test, False, f.error)
    report.check("suite", envelope.passed,
                 "all declared tests passed" if envelope.passed
                 else f"{len(envelope.failures)} declared failure(s)")
    return report
```

`report.check(item, ok, note)` appends and returns the report, so a single-item gate is one line: `return GateReport().check(command, ok, f"exit {code}")`.

**Write a note on passing checks too, not just failures.** The note is the evidence, and it is what makes a green gate worth reading — `artifacts_exist ✓ 1 checked · plan.md — exists, 454B` tells you what was verified, where a bare ✓ tells you nothing. Notes on failed checks double as the reason and are what the agent is told, so phrase them as the problem: `"claimed changed file does not exist"`.

Rules that keep gates honest:

- **Verify claims, never predict.** File names and counts are unknowable before the agent finishes; gates check what the envelope declared.
- **Quantity as properties, not counts.** "at least one artifact", "ALL declared paths exist" — never `len(artifacts) == 3`.
- **Record checks, don't raise.** The harness feeds the derived violations back into the same session as a correction — context intact, bounded by the phase's `retries` — and traces every check, passed or failed, to `gate_results.checks_json` and the `gate_pass`/`gate_fail` event payload.
- **Check every item, even after one fails.** Don't early-return on the first problem; the agent fixes more per correction round when it sees every failure at once, and the trace shows the full picture.
- **Don't gate the ungateable.** Plan quality and code taste are a reviewer agent's job or a human's.

A gate that returns a plain `list[str]` of violations still works — the harness adapts it — but it records no evidence for the items that passed, so prefer a `GateReport`.

Reusable gates live in `gates.py`; genuine one-offs can be defined inline at the ADW call site and passed in `gates=[...]`.

## Before you finish

Run the smoke ADW — `uv run adws/adw_prompt.py "ping"` — since every module change rides the same path a real run does.
