"""Trials: one request, several rosters, the best one merged.

`just trial "<request>" main chatgpt` starts one adw_simple_sdlc worktree run
per roster, all from the same commit, tagged with a shared trial id. Once they
have all ended, a panel run reviews what each one built, blind, with one
reviewer per model family. The scorecard suggests the cheapest attempt that
passed every gate; the engineer picks, and `just trial-merge` lands it and
drops the rest.

Everything a trial knows lives in the trace, as events:
  trial            on each attempt, at its start (session.ensure, from SSSF_TRIAL)
  trial_panel      on the panel run: the families reviewing, the attempts reviewed
  trial_diffs      on the panel run: each reviewed attempt's diff size
  trial_verdicts   on the panel run: one reviewer's verdicts and ranking
  trial_scorecard  on the panel run: the scorecard it ended with, for the dashboard
  trial_merged     on the winner

Design: docs/designs/trials.md in the SSSF repo.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import string
import subprocess
import sys
import time
from pathlib import Path
from statistics import mean

from . import agent_cc, agent_pi, agents, budget, gates, kill, repo_settings, worktree
from .data_types import (AgentCall, EventRecord, PanelOutput, PhaseParams, PromptEngineering,
                         SSSFConfig, TrialAttempt, TrialAttempts)
from .tracer import Tracer
from .utils import just, new_id

CHAIN = "adw_simple_sdlc"
REQUIRED_AGENTS = ["planner", "builder", "reviewer", "documenter"]
CONFIG_DIR = "adws/adw_sssf_config"
MAIN = "main"
PANEL_PROMPTS = "adws/adw_data/prompt_engineering/referee"
START_SECONDS = 600     # how long an attempt may take to open its run, uv sync included
POLL_SECONDS = 1.0


class TrialError(RuntimeError):
    """A trial that cannot start, be reviewed, or land as asked. Nothing was changed."""


# ── rosters ──────────────────────────────────────────────────────────────────

def roster_file(name: str) -> str:
    """The repo-relative path of a named roster. `main` is the repo's own."""
    return f"{CONFIG_DIR}/sssf.config.yaml" if name == MAIN else f"{CONFIG_DIR}/sssf.config.{name}.yaml"


def roster_names(root: Path) -> list[str]:
    found = sorted(p.name[len("sssf.config."):-len(".yaml")]
                   for p in (root / CONFIG_DIR).glob("sssf.config.*.yaml"))
    return [MAIN, *found]


def roster_path(root: Path, name: str) -> Path:
    path = root / roster_file(name)
    if not path.is_file():
        raise TrialError(
            f"there is no roster {name!r}: {roster_file(name)} does not exist. Rosters here: "
            f"{', '.join(roster_names(root))}. Add one with `uv run .claude/skills/sssf/scripts/"
            f"install.py --add-roster chatgpt` (or claude_code, or pi).")
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def provider(agent) -> str:
    if agent.coding_agent == "claude_code":
        return "anthropic"
    if "/" in agent.model:
        return agent.model.split("/", 1)[0]
    return agent_pi.resolve_model(agent.model)[0]


CODING_AGENTS = {"claude_code": "Claude Code", "pi": "pi"}


def coding_agent(cfg) -> str:
    """What the roster's agents run on, for its label: "Claude Code", "pi", or
    both joined when a roster mixes them."""
    return " + ".join(CODING_AGENTS.get(name, name)
                      for name in sorted({agent.coding_agent for agent in cfg.agents}))


def roster_label(attempt: dict) -> str:
    """`main (Claude Code)`: the roster's name and what it runs on. A trial
    from before the coding agent was recorded shows the name alone."""
    agent = attempt.get("coding_agent")
    return f"{attempt['roster']} ({agent})" if agent else attempt["roster"]


def family(agent) -> str:
    """Who made the model: what a panel reviewer could be partial to, and
    whose plan a run's usage counts against."""
    made_by = provider(agent)
    return "claude" if made_by == "anthropic" else made_by


def pi_problems(made_by: str) -> list[str]:
    """Why Pi can't run on this provider without billing the wrong account, or []."""
    try:
        done = subprocess.run([agent_pi.PI_PATH, "auth", "check", "--provider", made_by, "--json"],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env=agent_pi.child_env(), timeout=60)
        status = json.loads(done.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        return [f"`pi auth check --provider {made_by}` gave no answer: {error}"]
    if status.get("status") != "ready":
        return [f"pi is not signed in to {made_by} ({status.get('reason', 'not ready')})"]
    if made_by == "openai":
        if status.get("authType") != "oauth":
            return ["pi reaches openai with an API key, not the ChatGPT sign-in: run `pi`, "
                    "then /login, then openai"]
        if os.environ.get("SSSF_PI_AUTH") != "subscription":
            return ["SSSF_PI_AUTH is not `subscription` in .env, so a lapsed ChatGPT sign-in "
                    "would fall back to billing the API"]
    return []


def problems(cfg: SSSFConfig, name: str) -> list[str]:
    """Everything that would stop this roster's attempt, or []. Checked for
    every roster before any attempt starts."""
    try:
        agents.validate(cfg, REQUIRED_AGENTS)
    except SystemExit as error:
        return [f"{name}: {error}"]
    used = [agents.resolve(cfg, n) for n in REQUIRED_AGENTS]
    found = []
    if any(a.coding_agent == "claude_code" for a in used):
        try:
            agent_cc.check_login()
        except agent_cc.NotLoggedIn as error:
            found.append(f"{name}: {error}")
    for made_by in sorted({provider(a) for a in used if a.coding_agent == "pi"}):
        found += [f"{name}: {p}" for p in pi_problems(made_by)]
    return found


# ── the trace ────────────────────────────────────────────────────────────────

def _query(db: str, sql: str, *params) -> list[tuple]:
    Tracer(db, os.devnull).conn.close()     # an older trace gets its columns first
    return worktree._query(db, sql, *params)


def attempts(db: str, trial: str) -> list[dict]:
    """Every attempt of a trial, in launch order, with its run's totals."""
    rows = _query(db, "select e.adw_id, e.payload_json, s.status, s.total_cost, s.total_tokens, "
                      "s.started_at, s.ended_at, s.request from events e "
                      "left join sessions s on s.adw_id = e.adw_id "
                      "where e.name = 'trial' and json_extract(e.payload_json, '$.trial') = ?", trial)
    found = []
    for adw_id, payload, status, cost, tokens, started, ended, request in rows:
        found.append({**json.loads(payload), "adw_id": adw_id, "status": status,
                      "cost": float(cost or 0), "tokens": int(tokens or 0),
                      "started_at": started, "ended_at": ended, "request": request or ""})
    return sorted(found, key=lambda a: a.get("index", 0))


def _checks(db: str, adw_id: str) -> bool | None:
    """Whether the run's last full check passed: verify_N, or reverify after a revision."""
    rows = _query(db, "select json_extract(payload_json, '$.passed') from events "
                      "where adw_id = ? and type = 'log' and (name like 'verify\\_%' escape '\\' "
                      "or name = 'reverify') and json_extract(payload_json, '$.passed') is not null "
                      "order by rowid desc limit 1", adw_id)
    return bool(rows[0][0]) if rows else None


def _panel_events(db: str, trial: str) -> tuple[str | None, dict[str, list[dict]]]:
    """The latest panel run of a trial, and its events by name."""
    runs = _query(db, "select adw_id from events where name = 'trial_panel' and "
                      "json_extract(payload_json, '$.trial') = ? order by rowid desc limit 1", trial)
    if not runs:
        return None, {}
    panel = runs[0][0]
    events: dict[str, list[dict]] = {}
    for name, payload in _query(db, "select name, payload_json from events where adw_id = ? and "
                                    "name like 'trial\\_%' escape '\\' order by rowid", panel):
        events.setdefault(name, []).append(json.loads(payload))
    return panel, events


def _seconds(started: str | None, ended: str | None) -> float | None:
    if not started or not ended:
        return None
    from datetime import datetime
    return (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds()


def combine(rows: list[dict], families: list[str], verdicts: list[dict],
            panel_ran: bool) -> dict:
    """The gates, the ranks, and the suggestion, from the attempts and the panel's verdicts.

    `rows` are attempts with `status`, `checks`, `cost`, and `family`.
    `verdicts` are trial_verdicts payloads: a family, its labels, and its
    rulings and ranking by label. An attempt passes when its run succeeded, its
    last check passed, and no reviewer blocked it. It is unknown when a
    reviewer that should have ruled on it didn't.
    """
    by_family = {v["family"]: v for v in verdicts}
    cards = []
    for row in rows:
        ran = row["status"] == "success" and row["checks"] is True
        rulings, ranks, others = [], [], []
        for fam in families:
            seen = by_family.get(fam)
            label = next((lab for lab, adw in (seen or {}).get("labels", {}).items()
                          if adw == row["adw_id"]), None)
            if not seen or label is None:
                continue
            ruling = next((v for v in seen.get("verdicts", []) if v.get("label") == label), None)
            if ruling:
                rulings.append({"family": fam, "label": label,
                                "approved": bool(ruling.get("approved")),
                                "blocking": list(ruling.get("blocking", [])),
                                "unmet": [f.get("requirement", "") for f in ruling.get("findings", [])
                                          if not f.get("met")]})
            ranking = seen.get("ranking", [])
            if label in ranking:
                place = ranking.index(label) + 1
                ranks.append(place)
                if fam != row.get("family"):
                    others.append(place)
        if not ran:
            review = None
        elif panel_ran and len(rulings) == len(families):
            review = "pass" if all(r["approved"] for r in rulings) else "block"
        else:
            review = "unknown"
        cards.append({**row, "review": review, "rulings": rulings,
                      "rank": round(mean(ranks), 2) if ranks else None,
                      "rank_others": round(mean(others), 2) if others else None,
                      "passed": ran and review == "pass"})
    passed = [c for c in cards if c["passed"]]
    if any(c["review"] == "unknown" for c in cards):
        suggested, why = None, "the panel did not rule on every attempt that ran clean, so there is no suggestion"
    elif passed:
        best = min(passed, key=lambda c: c["cost"])
        suggested, why = best["adw_id"], f"{best['adw_id']}, {roster_label(best)}, the cheapest attempt to pass every gate"
    else:
        suggested, why = None, "no attempt passed every gate"
    return {"attempts": cards, "suggested": suggested, "why": why}


def scorecard(db: str, trial: str) -> dict:
    """Everything the trace says about a trial, combined. A panel that hasn't
    run yet, or failed, leaves the review gate pending or unknown."""
    rows = attempts(db, trial)
    if not rows:
        raise TrialError(f"there is no trial {trial} in {db}")
    panel, events = _panel_events(db, trial)
    live = set(worktree.live_runs(db))
    sizes = (events.get("trial_diffs") or [{}])[-1].get("sizes", {})
    for row in rows:
        row["checks"] = _checks(db, row["adw_id"])
        row["seconds"] = _seconds(row["started_at"], row["ended_at"])
        row["live"] = row["adw_id"] in live
        row.update(sizes.get(row["adw_id"], {}))
    families = list(dict.fromkeys(r.get("family", "") for r in rows))
    panel_info = (events.get("trial_panel") or [{}])[-1]
    card = combine(rows, panel_info.get("families", families),
                   events.get("trial_verdicts", []), panel is not None)
    merged = _query(db, "select adw_id from events where name = 'trial_merged' and "
                        "json_extract(payload_json, '$.trial') = ?", trial)
    panels = _query(db, "select s.adw_id, s.status, s.total_cost from sessions s where s.adw_id in "
                        "(select adw_id from events where name = 'trial_panel' and "
                        "json_extract(payload_json, '$.trial') = ?)", trial)
    panel_cost = sum(float(c or 0) for _, _, c in panels)
    status = next((s for a, s, _ in panels if a == panel), None)
    return {"trial": trial, "request": rows[0]["request"], "base": rows[0].get("base", ""),
            "families": panel_info.get("families", families), "panel": panel,
            "panel_status": status, "panel_cost": panel_cost,
            "total_cost": round(sum(r["cost"] for r in rows) + panel_cost, 4),
            "merged": merged[0][0] if merged else None,
            "running": any(r["live"] for r in rows), **card}


def _money(value: float) -> str:
    return f"${value:.2f}"


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m{secs:02d}s"


def render(card: dict) -> str:
    """The scorecard as a terminal table."""
    head = ["attempt", "roster", "run", "checks", "panel", "rank", "rank*", "cost", "time",
            "diff", "tokens"]
    lines = []
    for a in card["attempts"]:
        diff = f"+{a['added']} -{a['removed']}" if "added" in a else "-"
        lines.append([
            a["adw_id"] + (" <" if a["adw_id"] == card["suggested"] else ""),
            roster_label(a), "live" if a["live"] else (a["status"] or "?"),
            {True: "pass", False: "fail", None: "-"}[a["checks"]],
            a["review"] or "-", "-" if a["rank"] is None else str(a["rank"]),
            "-" if a["rank_others"] is None else str(a["rank_others"]),
            _money(a["cost"]), _duration(a["seconds"]), diff, f"{a['tokens']:,}"])
    widths = [max(len(str(r[i])) for r in [head, *lines]) for i in range(len(head))]
    out = [f"trial {card['trial']}: {card['request'][:100]}", ""]
    for r in [head, *lines]:
        out.append("  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip())
    out.append("")
    for a in card["attempts"]:
        for ruling in a["rulings"]:
            reasons = ruling["blocking"] + [f"unmet: {u}" for u in ruling["unmet"]]
            if not ruling["approved"]:
                out.append(f"{a['adw_id']} blocked by the {ruling['family']} reviewer: "
                           + "; ".join(reasons)[:400])
    out.append("rank is the average place across the panel, 1 is best; rank* leaves out "
               "the vote of the reviewer from the attempt's own family"
               + ("" if len(card["families"]) > 1 else ", so with one family it is empty"))
    again = just(f"trial-review {card['trial']}")
    if card["running"]:
        out.append("attempts are still running, so there is no panel yet")
    elif card["panel"] is None:
        out.append(f"no panel has run: `{again}`")
    elif card["panel_status"] != "success":
        out.append(f"the panel run {card['panel']} did not finish; `{again}` runs it again")
    if card["merged"]:
        out.append(f"merged: {card['merged']}")
    elif not card["running"]:
        out.append(f"suggested: {card['why']}")
    out.append(f"total: {_money(card['total_cost'])}, every attempt plus the panel "
               f"({_money(card['panel_cost'])})")
    return "\n".join(out)


def show(trial: str) -> str:
    return render(scorecard(_main_config(worktree.main_checkout()).observability.db, trial))


# ── starting one ─────────────────────────────────────────────────────────────

def _log(cfg: SSSFConfig, adw_id: str, name: str, payload: dict) -> None:
    tracer = Tracer(cfg.observability.db, f"{cfg.defaults.data_root}/sessions/{adw_id}/events.jsonl")
    tracer.event(EventRecord(adw_id=adw_id, phase_id="", type="log", name=name, payload=payload))


def _main_config(root: Path) -> SSSFConfig:
    cfg = agents.load_config(str(roster_path(root, MAIN)))
    worktree.anchor(cfg, root)
    return cfg


def _started(db: str, adw_id: str) -> bool:
    return bool(_query(db, "select 1 from sessions where adw_id = ?", adw_id))


def _tail(path: Path, lines: int = 12) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").strip().splitlines()[-lines:])
    except OSError:
        return "(no output)"


def check(root: Path, names: list[str]) -> tuple[SSSFConfig, dict[str, SSSFConfig], list[str]]:
    """Everything that must hold before the first attempt starts. Returns the
    main config, each roster's config, and warnings; raises TrialError otherwise."""
    if len(names) < 2:
        raise TrialError("a trial races at least two attempts: name two rosters, or one twice")
    cfgs = {name: agents.load_config(str(roster_path(root, name))) for name in dict.fromkeys(names)}
    found = [p for name, cfg in cfgs.items() for p in problems(cfg, name)]
    if found:
        raise TrialError("not every roster is ready, so no attempt started:\n- " + "\n- ".join(found))
    cfg = _main_config(root)
    cap = repo_settings.max_worktree_runs(root)
    live = worktree.live_runs(cfg.observability.db)
    if len(live) + len(names) > cap:
        held = f", and {', '.join(live)} hold{'s' if len(live) == 1 else ''} " if live else ", and "
        raise TrialError(
            f"this trial needs {len(names)} worktree slots at once{held}max_worktree_runs in "
            f"{repo_settings.PATH} allows {cap}. Nothing started. Wait for a run to end, or "
            f"raise max_worktree_runs.")
    try:
        budget.check_launch(cfg)
    except (budget.LiveBudgetStop, budget.DailyBudgetStop, budget.MachineBudgetStop) as error:
        raise TrialError(f"{type(error).__name__}: {error}") from None
    if worktree.git("rev-parse", "--abbrev-ref", "HEAD", cwd=root).stdout.strip() == "HEAD":
        raise TrialError("a trial needs a branch to merge its winner into; HEAD is detached")
    warnings = []
    shared: dict[str, list[str]] = {}
    for name in names:
        shared.setdefault(family(agents.resolve(cfgs[name], "reviewer")), []).append(name)
    for fam, members in shared.items():
        if len(members) > 1 and fam in ("claude", "openai"):
            warnings.append(f"{', '.join(members)} all run on {fam}, so they share one plan's "
                            "usage window")
    return cfg, cfgs, warnings


def start(request: str, names: list[str], out=sys.stdout) -> str:
    """Launch every attempt, wait for them, run the panel, print the scorecard.
    Returns the trial id."""
    root = worktree.main_checkout()
    os.chdir(root)
    cfg, cfgs, warnings = check(root, names)
    db = cfg.observability.db
    trial = new_id(8)
    base = worktree.git("rev-parse", "HEAD", cwd=root).stdout.strip()
    print(f"trial {trial}: {len(names)} attempts from {base[:7]}", file=out)
    for warning in warnings:
        print(f"  note: {warning}", file=out)

    launched: list[tuple[dict, subprocess.Popen, Path]] = []
    for index, name in enumerate(names):
        adw_id = new_id(8)
        tag = {"trial": trial, "index": index, "roster": name, "roster_file": roster_file(name),
               "roster_sha256": sha256(root / roster_file(name)), "coding_agent": coding_agent(cfgs[name]),
               "family": family(agents.resolve(cfgs[name], "reviewer")), "base": base}
        log = root / cfg.defaults.data_dir / "sessions" / adw_id / "trial.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "SSSF_TRIAL": json.dumps(tag)}
        with log.open("w") as handle:
            # A session of its own, so stopping `just trial` leaves the attempts running.
            proc = subprocess.Popen(
                ["uv", "run", str(root / "adws" / f"{CHAIN}.py"), request,
                 "--config", roster_file(name), "--adw-id", adw_id, "--worktree"],
                cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=handle,
                stderr=subprocess.STDOUT, start_new_session=True)
        launched.append(({**tag, "adw_id": adw_id}, proc, log))
        print(f"  {adw_id}  {roster_label(tag)}  (output: {log.relative_to(root)})", file=out)

    refused = _wait_for_starts(db, launched)
    if refused:
        _abandon(cfg, [a for a, _, _ in launched if a["adw_id"] not in refused])
        why = "\n".join(f"{adw_id}:\n{_tail(log)}" for (a, _, log) in launched
                        for adw_id in [a["adw_id"]] if adw_id in refused)
        raise TrialError(f"{', '.join(refused)} did not start, so the trial stopped the attempts "
                         f"that did and discarded their branches:\n{why}")

    print(f"every attempt is running; `{just(f'trial-show {trial}')}` shows where they are", file=out)
    for attempt, proc, _ in launched:
        proc.wait()
        row = next((r for r in attempts(db, trial) if r["adw_id"] == attempt["adw_id"]), {})
        print(f"  {attempt['adw_id']}  {roster_label(attempt)}: {row.get('status')}, "
              f"{_money(row.get('cost', 0))}", file=out)
    review(trial, out=out)
    return trial


def _wait_for_starts(db: str, launched) -> list[str]:
    """Wait until every attempt has opened its run. Returns the ones that
    exited without one: refused at launch."""
    deadline = time.monotonic() + START_SECONDS
    pending = {a["adw_id"]: proc for a, proc, _ in launched}
    refused = []
    while pending and time.monotonic() < deadline:
        for adw_id, proc in list(pending.items()):
            if _started(db, adw_id):
                del pending[adw_id]
            elif proc.poll() is not None:
                refused.append(adw_id)
                del pending[adw_id]
        if pending:
            time.sleep(POLL_SECONDS)
    for adw_id, proc in pending.items():
        proc.terminate()
        refused.append(adw_id)
    return refused


def _abandon(cfg: SSSFConfig, started: list[dict]) -> None:
    """Stop the attempts that started and drop their branches."""
    db = cfg.observability.db
    for attempt in started:
        kill.kill(db, attempt["adw_id"])
    for attempt in started:
        if worktree.recorded(db, attempt["adw_id"]):
            try:
                worktree.discard(cfg, attempt["adw_id"])
            except worktree.WorktreeError:
                pass


# ── the panel ────────────────────────────────────────────────────────────────

def diff_args(base: str, branch: str, data_dir: str) -> list[str]:
    return [f"{base}..{branch}", "--", ".", ":(exclude)specs", f":(exclude){data_dir}"]


def diff_size(root: Path, base: str, branch: str, data_dir: str) -> dict:
    out = worktree.git("diff", "--numstat", *diff_args(base, branch, data_dir), cwd=root).stdout
    added = removed = files = 0
    for line in out.splitlines():
        a, r, _ = line.split("\t", 2)
        files += 1
        added += int(a) if a.isdigit() else 0
        removed += int(r) if r.isdigit() else 0
    return {"added": added, "removed": removed, "files": files}


def panel_reviewers(root: Path, rows: list[dict]) -> dict[str, object]:
    """One reviewer per family: that family's first roster's reviewer, with
    the panel's own prompts and no write access."""
    reviewers: dict[str, object] = {}
    for row in rows:
        fam = row.get("family", "")
        if fam in reviewers:
            continue
        cfg = agents.load_config(str(roster_path(root, row["roster"])))
        reviewer = agents.resolve(cfg, "reviewer")
        reviewers[fam] = reviewer.model_copy(update={
            "name": f"referee_{fam}",
            "purpose": "Judge every attempt at a trial's request, blind, and rank them.",
            "prompt_engineering": PromptEngineering(system=f"{PANEL_PROMPTS}/system.md",
                                                   user=f"{PANEL_PROMPTS}/user.md"),
            "writes": []})
    return reviewers


LABELS = string.ascii_uppercase


def review(trial: str, out=sys.stdout) -> int:
    """Run the panel over a trial's finished attempts, then print the scorecard."""
    from . import session
    root = worktree.main_checkout()
    os.chdir(root)
    cfg = _main_config(root)
    db = cfg.observability.db
    rows = attempts(db, trial)
    if not rows:
        raise TrialError(f"there is no trial {trial} in {db}")
    live = [r["adw_id"] for r in rows if r["adw_id"] in set(worktree.live_runs(db))]
    if live:
        raise TrialError(f"{', '.join(live)} {'is' if len(live) == 1 else 'are'} still going; "
                         f"the panel reviews a trial once every attempt has ended")
    reviewers = panel_reviewers(root, rows)
    cfg.agents = [a for a in cfg.agents if a.name not in {r.name for r in reviewers.values()}]
    cfg.agents += list(reviewers.values())
    gone = [r["adw_id"] for r in rows if r["status"] == "success" and not worktree.git(
        "rev-parse", "--verify", "--quiet", f"refs/heads/{worktree.branch_for(r['adw_id'])}",
        cwd=root, check=False).stdout.strip()]
    if gone:
        raise TrialError(f"{', '.join(gone)} no longer {'has its' if len(gone) == 1 else 'have their'} "
                         "branch, so the panel can't see what was built")
    reviewed = [r for r in rows if r["status"] == "success" and _checks(db, r["adw_id"]) is True]

    run = session.ensure(cfg)
    families = list(reviewers)
    failed: BaseException | None = None
    with run.phase(PhaseParams(name="request", kind="engineer", owner=run.engineer,
                               description="The trial whose attempts the panel reviews")) as ph:
        ph.log(input=f"panel for trial {trial}: {rows[0]['request']}", trial=trial,
               attempts=len(rows), reviewed=len(reviewed))
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id="", type="log", name="trial_panel",
                                 payload={"trial": trial, "families": families,
                                          "reviewed": [r["adw_id"] for r in reviewed]}))
    with run.phase(PhaseParams(name="diffs", kind="code", owner="git",
                               description="Each reviewed attempt's diff from the trial's base")) as ph:
        sizes = {r["adw_id"]: diff_size(root, r["base"], worktree.branch_for(r["adw_id"]),
                                        cfg.defaults.data_dir) for r in reviewed}
        run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id=ph.phase.phase_id, type="log",
                                     name="trial_diffs", payload={"trial": trial, "sizes": sizes}))
        ph.log(**{r["adw_id"]: f"+{sizes[r['adw_id']]['added']} -{sizes[r['adw_id']]['removed']}"
                  for r in reviewed})

    try:
        for fam in families if reviewed else []:
            order = random.sample(reviewed, len(reviewed))
            labels = {LABELS[i]: r["adw_id"] for i, r in enumerate(order)}
            folder = run.context_handoff_dir / f"panel_{fam}"
            folder.mkdir(parents=True, exist_ok=True)
            shown = []
            for label, adw_id in labels.items():
                row = next(r for r in rows if r["adw_id"] == adw_id)
                text = worktree.git("diff", *diff_args(row["base"], worktree.branch_for(adw_id),
                                                       cfg.defaults.data_dir), cwd=root).stdout
                (folder / f"{label}.diff").write_text(text)
                shown.append(TrialAttempt(label=label, diff_path=str(folder / f"{label}.diff")))
            with run.phase(PhaseParams(name=f"panel_{fam}", kind="agent", owner=f"referee_{fam}",
                                       description=f"The {fam} reviewer judges every attempt, "
                                                   "blind, and ranks them")) as ph:
                verdict = ph.call(AgentCall(
                    output_type=PanelOutput, prompt=rows[0]["request"],
                    previous=TrialAttempts(status="success",
                                           summary=f"{len(shown)} attempts at the same request",
                                           attempts=shown),
                    gates=[gates.artifacts_exist, gates.panel_complete(list(labels))]))
                run.tracer.event(EventRecord(
                    adw_id=run.adw_id, phase_id=ph.phase.phase_id, type="log", name="trial_verdicts",
                    payload={"trial": trial, "family": fam, "labels": labels,
                             "verdicts": [v.model_dump() for v in verdict.verdicts],
                             "ranking": verdict.ranking}))
    except Exception as error:      # the scorecard still records what the panel got through
        failed = error

    # A failed phase has already closed the run; a clean panel closes it here,
    # so the scorecard it records names the panel's final status.
    code = 1 if failed is not None else run.finish()
    card = scorecard(db, trial)
    run.tracer.event(EventRecord(adw_id=run.adw_id, phase_id="", type="log", name="trial_scorecard",
                                 payload=card))
    print("\n" + render(card), file=out)
    if failed is not None:
        raise failed
    return code


# ── landing one ──────────────────────────────────────────────────────────────

def _ready_to_land(cfg: SSSFConfig, trial: str) -> list[dict]:
    db = cfg.observability.db
    rows = attempts(db, trial)
    if not rows:
        raise TrialError(f"there is no trial {trial} in {db}")
    live = [r["adw_id"] for r in rows if r["adw_id"] in set(worktree.live_runs(db))]
    if live:
        raise TrialError(f"{', '.join(live)} {'is' if len(live) == 1 else 'are'} still going. "
                         f"Wait for {'it' if len(live) == 1 else 'them'}, or stop "
                         f"{'it' if len(live) == 1 else 'them'} with `{just('kill <adw_id>')}`.")
    return rows


def _drop(cfg: SSSFConfig, rows: list[dict]) -> list[str]:
    root = worktree.main_checkout()
    done = []
    for row in rows:
        branch = worktree.branch_for(row["adw_id"])
        if worktree.git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", cwd=root,
                        check=False).returncode == 0:
            done.append(worktree.discard(cfg, row["adw_id"]))
    return done


def merge(trial: str, adw_id: str, ff_only: bool) -> str:
    """Land the engineer's pick the way `just merge` does, then drop the other
    attempts. A merge that fails discards nothing."""
    root = worktree.main_checkout()
    cfg = _main_config(root)
    rows = _ready_to_land(cfg, trial)
    if adw_id not in {r["adw_id"] for r in rows}:
        raise TrialError(f"{adw_id} is not an attempt of trial {trial}: "
                         f"{', '.join(r['adw_id'] for r in rows)}")
    done = [worktree.merge(cfg, adw_id, ff_only)]
    _log(cfg, adw_id, "trial_merged", {"trial": trial})
    done += _drop(cfg, [r for r in rows if r["adw_id"] != adw_id])
    return "\n".join(done)


def discard(trial: str) -> str:
    """Drop every attempt of a trial. The traces and the scorecard stay."""
    root = worktree.main_checkout()
    cfg = _main_config(root)
    done = _drop(cfg, _ready_to_land(cfg, trial))
    return "\n".join(done) or f"trial {trial} has no branches left to drop"
