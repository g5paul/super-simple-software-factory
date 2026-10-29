"""Pi on a subscription sign-in never falls back to an API key.

Pi prefers its own OAuth sign-in (a ChatGPT or Claude subscription) over an
API key in the environment, so a key in .env does no harm while the sign-in
holds. When the sign-in lapses, Pi uses the key and bills the API without a
word. With SSSF_PI_AUTH=subscription, SSSF hands Pi no key for a provider that
has a subscription sign-in, so a lapsed sign-in fails the call instead.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_pi

KEYS = {"OPENAI_API_KEY": "sk-x", "ANTHROPIC_API_KEY": "sk-y", "OPENROUTER_API_KEY": "sk-z"}


def test_by_default_pi_gets_every_key(monkeypatch):
    for var, value in KEYS.items():
        monkeypatch.setenv(var, value)
    monkeypatch.delenv("SSSF_PI_AUTH", raising=False)
    env = agent_pi.child_env()
    assert all(env[var] == value for var, value in KEYS.items())


def test_on_a_subscription_pi_gets_no_key_a_sign_in_covers(monkeypatch):
    for var, value in KEYS.items():
        monkeypatch.setenv(var, value)
    monkeypatch.setenv("SSSF_PI_AUTH", "subscription")
    env = agent_pi.child_env()
    assert "OPENAI_API_KEY" not in env and "ANTHROPIC_API_KEY" not in env
    assert env["OPENROUTER_API_KEY"] == "sk-z"      # no sign-in for it, so the key stays
