"""Read a feature folder written by mattpocock/skills' `to-spec` and `to-tickets`.

The local markdown tracker keeps one folder per feature:

    .scratch/<feature>/spec.md               the spec, seams under "Testing Decisions"
    .scratch/<feature>/issues/NN-<slug>.md   one ticket per file

A ticket file starts `# NN: Title` and carries `**Blocked by:** 01, 03` (or
"None") and `**Status:** ready-for-agent`. This module is the read step of
adw_tickets: plain code that rejects a bad folder before any agent spends
money, orders the tickets so blockers come first, and reports which are done.
`mark_done` is the only write, and only code calls it, in the ticket's commit.
"""

from __future__ import annotations

import heapq
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

from .data_types import Feature, Ticket

TICKET_FILE = re.compile(r"^(\d{2,3})-.+\.md$")
TITLE = re.compile(r"^#\s*\d{2,3}\s*:\s*(.+?)\s*$", re.M)
BLOCKED_BY = re.compile(r"^\**Blocked by:?\**:?\s*(.*)$", re.M | re.I)
STATUS = re.compile(r"^(\**Status:?\**:?[ \t]*)(.*)$", re.M | re.I)
# A bare number, not part of a word or a version: "01", "3", but not "OAuth2" or "v1.5".
NUMBER = re.compile(r"(?<![\w.])(\d{1,3})(?![\w.])")


# adw_tickets names a ticket's phases t01_build, t01_verify_1, t01_fix_1, ...
TICKET_PHASE = re.compile(r"_\d{2,}_t(\d{2,3})_")
TICKET_RUNS = "s.adw_name LIKE '%adw_tickets%'"


class FeatureError(ValueError):
    pass


def load(folder: str | Path) -> Feature:
    folder = Path(folder)
    spec = folder / "spec.md"
    if not spec.is_file():
        raise FeatureError(f"{folder}: no spec.md. Write it with /to-spec first.")
    _require_seams(spec)
    issues = folder / "issues"
    files = sorted(p for p in issues.glob("*.md") if TICKET_FILE.match(p.name)) if issues.is_dir() else []
    if not files:
        raise FeatureError(f"{folder}: no tickets in issues/. Write them with /to-tickets first.")
    found = [_ticket(p) for p in files]
    return Feature(slug=folder.name, folder=str(folder), spec=str(spec), tickets=_order(found))


def mark_done(ticket: Ticket) -> None:
    """Set the ticket's Status line to done, changing nothing else in the file."""
    path = Path(ticket.path)
    text = path.read_text()
    if STATUS.search(text):
        text = STATUS.sub(lambda m: m.group(1) + "done", text, count=1)
    else:
        lines = text.splitlines(keepends=True)
        lines[1:1] = ["\n", "**Status:** done\n"]
        text = "".join(lines)
    path.write_text(text)


def _require_seams(spec: Path) -> None:
    text = spec.read_text()
    match = re.search(r"^##\s+Testing Decisions\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    if not match or not match.group(1).strip():
        raise FeatureError(
            f"{spec}: the Testing Decisions section is missing or empty. It holds the "
            f"test seams agreed in /to-spec, and the builders test only there.")


def _ticket(path: Path) -> Ticket:
    text = path.read_text()
    number = TICKET_FILE.match(path.name).group(1)
    title = TITLE.search(text)
    blocked = BLOCKED_BY.search(text)
    status = STATUS.search(text)
    edges = []
    if blocked and not blocked.group(1).strip().lower().startswith("none"):
        edges = [n.zfill(len(number)) for n in NUMBER.findall(blocked.group(1))]
    return Ticket(number=number, slug=path.stem, path=str(path),
                  title=title.group(1) if title else path.stem,
                  blocked_by=list(dict.fromkeys(edges)),
                  done=bool(status) and status.group(2).strip().strip("*").lower() == "done")


def _order(found: list[Ticket]) -> list[Ticket]:
    """Blockers first; tickets free at the same moment keep their numbered order."""
    by_number = {t.number: t for t in found}
    for t in found:
        for edge in t.blocked_by:
            if edge not in by_number or edge == t.number:
                raise FeatureError(f"{Path(t.path).name} is blocked by {edge}, "
                                   f"which is not another ticket in this folder.")
    waiting = {t.number: set(t.blocked_by) for t in found}
    ready = [n for n, edges in waiting.items() if not edges]
    heapq.heapify(ready)
    ordered = []
    while ready:
        number = heapq.heappop(ready)
        ordered.append(by_number[number])
        for other, edges in waiting.items():
            if number in edges:
                edges.discard(number)
                if not edges:
                    heapq.heappush(ready, other)
    if len(ordered) < len(found):
        stuck = sorted(n for n, edges in waiting.items() if edges)
        raise FeatureError(f"the blocking edges form a cycle, so tickets {', '.join(stuck)} "
                           f"can never start.")
    return ordered


# ── cost history, from the trace db ─────────────────────────────────────────

def spent_on(db: str | Path, folder: str) -> float:
    """What every adw_tickets run on this feature folder has cost so far."""
    with sqlite3.connect(db) as conn:
        row = conn.execute(f"SELECT COALESCE(SUM(total_cost), 0) FROM sessions s "
                           f"WHERE {TICKET_RUNS} AND request = ?", (folder,)).fetchone()
    return float(row[0])


def estimate(db: str | Path, remaining: int) -> str:
    """A rough cost for `remaining` tickets, from past ticket runs. It informs;
    max_run_cost is what stops a run."""
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            f"SELECT e.adw_id, e.phase_id, json_extract(e.payload_json, '$.cost') "
            f"FROM events e JOIN sessions s ON s.adw_id = e.adw_id "
            f"WHERE {TICKET_RUNS} AND e.type = 'agent_end'").fetchall()
    per_ticket: dict = defaultdict(float)
    overhead: dict = defaultdict(float)
    for adw_id, phase_id, cost in rows:
        match = TICKET_PHASE.search(phase_id or "")
        if match:
            per_ticket[(adw_id, match.group(1))] += cost or 0.0
        else:
            overhead[adw_id] += cost or 0.0
    if remaining == 0:
        return "no tickets left to build"
    if not per_ticket:
        return "no history yet"
    ticket = sum(per_ticket.values()) / len(per_ticket)
    runs = {adw_id for adw_id, _ in per_ticket}
    rest = sum(overhead[r] for r in runs) / len(runs)
    return (f"about ${remaining * ticket + rest:.2f}: {remaining} ticket(s) at ${ticket:.2f} "
            f"each from {len(per_ticket)} past ticket(s), plus ${rest:.2f} for the scout, "
            f"review, and docs")
