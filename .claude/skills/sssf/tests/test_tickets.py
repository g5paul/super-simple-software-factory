"""The read step of adw_tickets: a feature folder in, ordered tickets out.

`to-spec` and `to-tickets` (mattpocock/skills, local markdown tracker) write
`.scratch/<feature>/spec.md` and one `issues/NN-<slug>.md` per ticket. The
read step is plain code: it checks the folder before any agent spends money,
orders tickets so blockers come first, and skips tickets already done.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import tickets

SPEC = """# Spec

## Problem Statement

Users want X.

## Testing Decisions

- Test through the public `summarize()` seam only.

## Out of Scope

Nothing.
"""


def ticket(title: str, blocked_by: str = "None (can start immediately)",
           status: str = "ready-for-agent", number: str = "01") -> str:
    return f"""# {number}: {title}

**What to build:** something end to end.

**Blocked by:** {blocked_by}

**Status:** {status}

- [ ] It works
"""


@pytest.fixture
def feature(tmp_path):
    folder = tmp_path / ".scratch" / "summaries"
    (folder / "issues").mkdir(parents=True)
    (folder / "spec.md").write_text(SPEC)

    def add(name: str, title: str, **kwargs) -> Path:
        path = folder / "issues" / f"{name}.md"
        path.write_text(ticket(title, number=name[:2], **kwargs))
        return path

    add.folder = folder
    return add


def load(add):
    return tickets.load(add.folder)


def test_tickets_come_back_blockers_first(feature):
    feature("01-schema", "Add the schema")
    feature("02-api", "Serve it", blocked_by="03")
    feature("03-cache", "Cache it", blocked_by="01 (Add the schema)")
    loaded = load(feature)
    assert loaded.slug == "summaries"
    assert [t.number for t in loaded.tickets] == ["01", "03", "02"]
    assert loaded.tickets[0].title == "Add the schema"
    assert loaded.tickets[2].blocked_by == ["03"]


def test_unrelated_tickets_keep_their_numbered_order(feature):
    feature("02-b", "B")
    feature("01-a", "A")
    feature("03-c", "C", blocked_by="01, 02")
    assert [t.number for t in load(feature).tickets] == ["01", "02", "03"]


def test_numbers_inside_words_are_not_edges(feature):
    feature("01-a", "A")
    feature("02-b", "B", blocked_by="01 (Add OAuth2 and v1.5 support)")
    assert load(feature).tickets[1].blocked_by == ["01"]


def test_done_tickets_are_skipped(feature):
    feature("01-a", "A", status="done")
    feature("02-b", "B", blocked_by="01")
    loaded = load(feature)
    assert [t.number for t in loaded.todo] == ["02"]
    assert loaded.tickets[0].done


@pytest.mark.parametrize("spec, problem", [
    (None, "spec.md"),
    (SPEC.replace("- Test through the public `summarize()` seam only.\n", ""), "Testing Decisions"),
    (SPEC.replace("## Testing Decisions", "## Tests"), "Testing Decisions"),
])
def test_a_spec_without_seams_fails(feature, spec, problem):
    feature("01-a", "A")
    spec_path = feature.folder / "spec.md"
    if spec is None:
        spec_path.unlink()
    else:
        spec_path.write_text(spec)
    with pytest.raises(tickets.FeatureError, match=problem):
        load(feature)


def test_a_folder_with_no_tickets_fails(feature):
    with pytest.raises(tickets.FeatureError, match="no tickets"):
        load(feature)


def test_an_edge_to_a_missing_ticket_names_both(feature):
    feature("01-a", "A", blocked_by="07")
    with pytest.raises(tickets.FeatureError, match=r"01-a\.md.*07"):
        load(feature)


def test_a_cycle_names_its_tickets(feature):
    feature("01-a", "A")
    feature("02-b", "B", blocked_by="03")
    feature("03-c", "C", blocked_by="02")
    with pytest.raises(tickets.FeatureError, match=r"cycle.*02.*03"):
        load(feature)


def test_a_ticket_with_no_blocked_by_line_has_no_blockers(feature):
    path = feature("01-a", "A")
    path.write_text(path.read_text().replace("**Blocked by:** None (can start immediately)\n", ""))
    assert load(feature).tickets[0].blocked_by == []


def test_mark_done_changes_only_the_status_line(feature):
    path = feature("01-a", "A")
    before = path.read_text()
    tickets.mark_done(load(feature).tickets[0])
    after = path.read_text()
    assert "**Status:** done" in after
    assert after.replace("**Status:** done", "**Status:** ready-for-agent") == before
    assert load(feature).tickets[0].done


def test_mark_done_adds_a_status_line_when_there_is_none(feature):
    path = feature("01-a", "A")
    path.write_text(path.read_text().replace("**Status:** ready-for-agent\n", ""))
    tickets.mark_done(load(feature).tickets[0])
    assert path.read_text().splitlines()[:3] == ["# 01: A", "", "**Status:** done"]
