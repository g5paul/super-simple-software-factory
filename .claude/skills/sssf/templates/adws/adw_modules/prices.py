"""Claude's list prices, for a Claude Code call that never reported its own.

Claude Code prices a call only in its final result event. A call SSSF kills
mid-turn, for a rule breach or for usage credits, never sends one, so SSSF
prices its tokens here. prices.json holds the table; the dashboard's account
panel reads the same file.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

MILLION = 1_000_000


@lru_cache(maxsize=1)
def table() -> dict:
    return json.loads((Path(__file__).with_name("prices.json")).read_text())["models"]


def listed(model: str) -> bool:
    return model.split("/")[-1] in table()


def unlisted_warning(model: str) -> str:
    return (f"{model} isn't in adws/adw_modules/prices.json, so a call of it that SSSF "
            "kills adds its tokens and no cost, and the dashboard can't estimate it. "
            "Add its list price from https://claude.com/pricing to the skill's prices.json.")


def cost(model: str, usage: dict) -> Optional[float]:
    """One request's list price from its Anthropic `usage` block, or None for
    a model the table doesn't list. `model` may carry a provider prefix."""
    rates = table().get(model.split("/")[-1])
    if rates is None:
        return None
    # Without the 5m/1h split, a cache write is read as 1-hour, as the
    # dashboard reads transcripts: Claude Code caches for an hour.
    split = usage.get("cache_creation")
    tokens = {
        "input": usage.get("input_tokens") or 0,
        "output": usage.get("output_tokens") or 0,
        "cache_read": usage.get("cache_read_input_tokens") or 0,
        "cache_write_5m": (split.get("ephemeral_5m_input_tokens") or 0) if split else 0,
        "cache_write_1h": ((split.get("ephemeral_1h_input_tokens") or 0) if split
                           else usage.get("cache_creation_input_tokens") or 0),
    }
    prompt = tokens["input"] + tokens["cache_read"] + tokens["cache_write_5m"] + tokens["cache_write_1h"]
    long = rates.get("long_prompt")
    if long and prompt > long["over"]:
        rates = long
    return sum(tokens[kind] * rates[kind] for kind in tokens) / MILLION
