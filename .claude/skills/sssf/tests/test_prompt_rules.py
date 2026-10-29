"""Doc updates are part of the change, and every agent's prompt says so.

In the first real-repo run the builder updated README and CHANGELOG on its
own initiative. Nothing asked it to, so the next run could skip it, and the
reviewer had no reason to check. These rules make it the normal workflow:
the planner lists the affected docs, the builder updates them, the reviewer
checks them, and the documenter leaves them alone and writes its write-up.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent / "templates" / "prompt_engineering"


def system(agent: str) -> str:
    return (PROMPTS / agent / "system.md").read_text()


def test_planner_lists_the_affected_docs():
    assert "docs the change makes out of date" in system("planner")


def test_builder_updates_the_affected_docs():
    text = system("builder")
    assert "docs your change makes out of date" in text
    assert "CHANGELOG" in text and "README" in text


def test_reviewer_checks_the_docs():
    assert "were not updated, that requirement is not met" in system("reviewer")


def test_documenter_does_not_redo_the_builders_doc_edits():
    assert "do not edit them again" in system("documenter")


def test_subagent_sections_apply_only_with_the_tools():
    # Pi agents get subagent_* from the subagents.ts extension; Claude Code
    # agents never do. One prompt serves both, so the section says when it applies.
    for agent in ("planner", "scout"):
        section = system(agent).split("## Subagents", 1)[1]
        assert "If your tools do not include `subagent_create`, skip this section" in section, agent


def test_builder_leaves_the_spec_and_records_alone():
    # Ruff 0.16 formats Python blocks inside Markdown. A builder that ran
    # `ruff format .` rewrote a committed spec in a yt-summarizer run.
    text = system("builder")
    assert "The spec in `specs/` is read-only" in text
    assert "never on `specs/` or `app_docs/`" in text
