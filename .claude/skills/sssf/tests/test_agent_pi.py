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

import json
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_pi
from adw_modules.data_types import AgentRequest

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


# A call whose last assistant turn ended in an error fails with pi's own
# message. Without this, an error turn with no text reached the JSON parser,
# which sent corrections into the same error and then blamed the JSON.

def _fake_pi(tmp_path, monkeypatch, *messages, exit_code=0):
    events = tmp_path / "events.jsonl"
    events.write_text("".join(json.dumps({"type": "message_end", "message": m}) + "\n"
                              for m in messages))
    script = tmp_path / "pi"
    script.write_text(f"#!/bin/sh\ncat {events}\nexit {exit_code}\n")
    script.chmod(0o755)
    monkeypatch.setattr(agent_pi, "PI_PATH", str(script))
    monkeypatch.setattr(agent_pi, "resolve_model", lambda model: ("openai", "gpt"))
    monkeypatch.setattr(agent_pi, "context_window", lambda provider, model_id: 0)
    return AgentRequest(prompt="go", system_prompt="", model="openai/gpt",
                        session_id="s", session_dir=str(tmp_path / "sessions"),
                        raw_output_path=str(tmp_path / "raw.jsonl"), cwd=str(tmp_path))


def _turn(stop, text="", error=None, cost=0.0):
    message = {"role": "assistant", "stopReason": stop,
               "content": [{"type": "text", "text": text}] if text else [],
               "usage": {"input": 10, "output": 5, "cost": {"total": cost}}}
    if error:
        message["errorMessage"] = error
    return message


def test_an_error_turn_fails_the_call_with_pis_message(tmp_path, monkeypatch):
    request = _fake_pi(tmp_path, monkeypatch, _turn("error", error=(
        "429 subscription_sharing_usage_limit_exceeded: usage limit reached")))
    with pytest.raises(RuntimeError, match="subscription_sharing_usage_limit_exceeded"):
        agent_pi.run(request)


def test_an_error_after_text_still_fails(tmp_path, monkeypatch):
    request = _fake_pi(tmp_path, monkeypatch, _turn("toolUse", text="looking"),
                       _turn("error", error="500 server error"))
    with pytest.raises(RuntimeError, match="500 server error"):
        agent_pi.run(request)


def test_a_turn_that_recovers_after_an_error_succeeds(tmp_path, monkeypatch):
    request = _fake_pi(tmp_path, monkeypatch, _turn("error", error="529 overloaded"),
                       _turn("stop", text='{"ok": true}', cost=0.01))
    result = agent_pi.run(request)
    assert result.text == '{"ok": true}'
    assert result.cost == 0.01
