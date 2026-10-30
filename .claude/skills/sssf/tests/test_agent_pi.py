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


def test_pi_is_named_for_the_obs_extension(monkeypatch):
    monkeypatch.setenv("OBS_NAME", "the operator's name")
    assert agent_pi.child_env("builder")["OBS_NAME"] == "builder"
    assert agent_pi.child_env()["OBS_NAME"] == "the operator's name"


def test_each_run_gets_its_own_pool_and_tags(monkeypatch):
    monkeypatch.delenv("OBS_POOL", raising=False)
    monkeypatch.delenv("OBS_TAG", raising=False)
    env = agent_pi.child_env("scout", "d6af5329")
    assert env["OBS_POOL"] == "sssf-d6af5329"
    assert env["OBS_TAG"] == "sssf,adw-d6af5329"


def test_the_operators_pool_and_tags_are_kept(monkeypatch):
    monkeypatch.setenv("OBS_POOL", "sssf-pi-gpt")
    monkeypatch.setenv("OBS_TAG", "nightly, sssf")
    env = agent_pi.child_env("scout", "d6af5329")
    assert env["OBS_POOL"] == "sssf-pi-gpt"
    assert env["OBS_TAG"] == "nightly,sssf,adw-d6af5329"


def test_an_empty_operator_pool_still_gets_the_run_pool(monkeypatch):
    monkeypatch.setenv("OBS_POOL", "")
    assert agent_pi.child_env("scout", "d6af5329")["OBS_POOL"] == "sssf-d6af5329"
