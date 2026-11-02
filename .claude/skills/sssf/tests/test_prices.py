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

from adw_modules import prices

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
