"""Claude's list prices, for a Claude Code call that never reported its own.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from types import SimpleNamespace

from adw_modules import agent_cc, agents, prices

from test_agent_cc import fake, request  # noqa: F401  (fixture and helper)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

MILLION = 1_000_000


def test_each_token_kind_has_its_list_price():
    split = {"ephemeral_5m_input_tokens": MILLION, "ephemeral_1h_input_tokens": 0}
    assert prices.cost("claude-opus-5-5", {"input_tokens": MILLION}) == pytest.approx(4)
    assert prices.cost("claude-opus-5-5", {"output_tokens": MILLION}) == pytest.approx(20)
    assert prices.cost("claude-sonnet-5-5", {"cache_read_input_tokens": MILLION}) == pytest.approx(0.1)
    assert prices.cost("claude-fable-5-1", {"cache_creation": split}) == pytest.approx(12.5)


def test_a_cache_write_without_the_split_reads_as_one_hour():
    # As the dashboard reads transcripts: Claude Code caches for an hour.
    assert prices.cost("claude-opus-5-5", {"cache_creation_input_tokens": MILLION}) == pytest.approx(8)


def test_a_long_haiku_prompt_costs_five_times_as_much():
    assert prices.cost("claude-haiku-5-5", {"input_tokens": 100_000}) == pytest.approx(0.01)
    assert prices.cost("claude-haiku-5-5", {"input_tokens": 50_000, "cache_read_input_tokens": 60_000}) \
        == pytest.approx((50_000 * 0.5 + 60_000 * 0.05) / MILLION)


def test_a_provider_prefix_is_ignored_and_an_unknown_model_is_unpriced():
    assert prices.cost("anthropic/claude-haiku-5-5", {"output_tokens": MILLION}) == pytest.approx(0.5)
    assert prices.cost("claude-mystery-9", {"input_tokens": MILLION}) is None


def test_listed_reads_the_table():
    assert prices.listed("claude-opus-5-5") and prices.listed("anthropic/claude-fable-5-1")
    assert not prices.listed("claude-mystery-9")


def test_a_claude_call_names_the_models_that_answered(tmp_path, fake):
    fake(FIXTURES / "cc_turn1.jsonl")
    assert agent_cc.run(request(tmp_path)).models == ["claude-haiku-5-5"]


def test_an_unlisted_model_is_warned_about_once_per_run():
    said, events = [], []
    run = SimpleNamespace(adw_id="r1", console=SimpleNamespace(advise=said.append),
                          tracer=SimpleNamespace(event=events.append))
    phase = SimpleNamespace(phase_id="r1_01")
    for _ in range(2):
        agents.warn_unlisted(run, phase, "builder", ["claude-haiku-5-5", "claude-mystery-9"])
    assert len(said) == 1 and "claude-mystery-9" in said[0] and "prices.json" in said[0]
    assert [(e.name, e.payload["model"]) for e in events] == [("unlisted_model", "claude-mystery-9")]
