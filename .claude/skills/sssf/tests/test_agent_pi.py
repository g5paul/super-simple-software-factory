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
import os
import shutil
import socket
import sys
import tempfile
import threading
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_pi, agents, sandbox
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


def test_the_agent_knows_it_is_an_sssf_agent():
    env = agent_pi.child_env("scout", "d6af5329")
    assert (env["SSSF_ADW_ID"], env["SSSF_AGENT"]) == ("d6af5329", "scout")


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
    request = _fake_pi(tmp_path, monkeypatch, _turn("error", error="400 invalid_request_error: bad tool"))
    with pytest.raises(RuntimeError, match="400 invalid_request_error: bad tool"):
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


# At the ChatGPT plan's limit, OpenAI's token-sharing route answers 429 with
# subscription_sharing_usage_limit_exceeded, and pi never retries it. The call
# stops by name, so the run can say the plan is out rather than "an error".
# Pi writes the message two ways: with the status, or as a mid-stream
# response.failed with none. Both end with a link to the usage page.

@pytest.mark.parametrize("error", [
    'OpenAI API error (429): {"error":{"code":"subscription_sharing_usage_limit_exceeded",'
    '"message":"Usage limit reached"}}\nCheck your ChatGPT usage: https://chatgpt.com/settings/usage',
    "subscription_sharing_usage_limit_exceeded: Usage limit reached\n"
    "Check your ChatGPT usage: https://chatgpt.com/settings/usage",
])
def test_the_plan_limit_stops_by_name(tmp_path, monkeypatch, error):
    request = _fake_pi(tmp_path, monkeypatch, _turn("toolUse", text="reading"), _turn("error", error=error))
    request.adw_id = "a1b2c3d4"
    with pytest.raises(agent_pi.UsageLimitStop) as stop:
        agent_pi.run(request)
    message = str(stop.value)
    assert "usage limit" in message and agent_pi.USAGE_PAGE in message
    assert "--adw-id a1b2c3d4" in message
    assert "reset time" in message                  # OpenAI gives none, so SSSF guesses none


def test_usage_that_could_not_be_checked_is_an_ordinary_error(tmp_path, monkeypatch):
    # The 503 means OpenAI couldn't check usage; pi retries it itself. If it
    # still fails, the run must not claim the plan is out.
    request = _fake_pi(tmp_path, monkeypatch, _turn("error", error=(
        "OpenAI API error (503): subscription_sharing_usage_unavailable")))
    with pytest.raises(RuntimeError, match="subscription_sharing_usage_unavailable") as stop:
        agent_pi.run(request)
    assert not isinstance(stop.value, agent_pi.UsageLimitStop)


def test_a_turn_that_recovers_after_the_limit_is_not_stopped(tmp_path, monkeypatch):
    request = _fake_pi(tmp_path, monkeypatch,
                       _turn("error", error="subscription_sharing_usage_limit_exceeded"),
                       _turn("stop", text='{"ok": true}'))
    assert agent_pi.run(request).text == '{"ok": true}'


# --- the operator container: a token from the host's broker ---

@pytest.fixture
def broker(tmp_path, monkeypatch):
    """A fake broker answering one line; set `answer` before connecting."""
    import tempfile
    d = tempfile.mkdtemp(dir="/tmp")
    path = f"{d}/b.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(5)
    state = {"answer": b"OK eyJ-tok\n"}

    def serve():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            conn.sendall(state["answer"])
            conn.close()
    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setenv("SSSF_PI_TOKEN_SOCKET", path)
    monkeypatch.setattr(agent_pi, "PI_AUTH_JSON", tmp_path / "auth.json")
    yield state
    server.close()
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def test_the_container_gets_the_brokers_token(broker):
    env = agent_pi._in_container({"OBS_AUTH_TOKEN": "obs"}, sandbox.LOCAL)
    assert env["OPENAI_API_KEY"] == "eyJ-tok"
    assert env["OBS_AUTH_TOKEN"] == "obs" and "OBS_DISABLE" not in env


def test_a_subscription_run_still_gets_the_brokers_token(broker, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host")
    monkeypatch.setenv("SSSF_PI_AUTH", "subscription")
    env = agent_pi._in_container(agent_pi.child_env(), sandbox.LOCAL)
    assert env["OPENAI_API_KEY"] == "eyJ-tok"


def test_a_broker_error_stops_with_its_message(broker):
    broker["answer"] = b"ERR no sign-in\n"
    with pytest.raises(sandbox.SandboxFailed, match="no sign-in"):
        agent_pi._in_container({}, sandbox.LOCAL)


def test_a_missing_socket_stops(tmp_path, monkeypatch):
    monkeypatch.setenv("SSSF_PI_TOKEN_SOCKET", str(tmp_path / "none.sock"))
    monkeypatch.setattr(agent_pi, "PI_AUTH_JSON", tmp_path / "auth.json")
    with pytest.raises(sandbox.SandboxFailed, match="isn't answering"):
        agent_pi._in_container({}, sandbox.LOCAL)


def test_an_api_key_from_the_broker_is_refused_without_echoing_it(broker):
    broker["answer"] = b"OK sk-x\n"
    with pytest.raises(sandbox.SandboxFailed) as stop:
        agent_pi._in_container({}, sandbox.LOCAL)
    assert "sk-x" not in str(stop.value)


def test_a_host_run_is_unchanged(monkeypatch):
    monkeypatch.delenv("SSSF_PI_TOKEN_SOCKET", raising=False)
    env = {"A": "1"}
    assert agent_pi._in_container(env, sandbox.LOCAL) == {"A": "1"}


def test_a_remote_tree_is_untouched(broker):
    env = {"A": "1"}
    assert agent_pi._in_container(env, object()) == {"A": "1"}


def test_a_stored_login_check(tmp_path):
    path = tmp_path / "auth.json"
    agent_pi.check_stored_login(path)                       # missing: passes
    path.write_text('{"anthropic": {"key": "x"}}')
    agent_pi.check_stored_login(path)
    path.write_text('{"openai": {"access": "SECRET"}}')
    with pytest.raises(sandbox.SandboxFailed) as stop:
        agent_pi.check_stored_login(path)
    assert "SECRET" not in str(stop.value) and str(path) in str(stop.value)
    path.write_text("not json")
    with pytest.raises(sandbox.SandboxFailed, match="can't read"):
        agent_pi.check_stored_login(path)


def test_the_auth_path_follows_pi_and_ignores_PI_AUTH_PATH(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PI_AUTH_PATH", "/nonexistent/auth.json")
    monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    assert agent_pi._pi_auth_json() == tmp_path / ".pi/agent/auth.json"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "x"))
    assert agent_pi._pi_auth_json() == tmp_path / "x/auth.json"
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "~/y")
    assert agent_pi._pi_auth_json() == tmp_path / "y/auth.json"
    assert "PI_AUTH_PATH" not in Path(agent_pi.__file__).read_text()


def test_a_relative_agent_dir_stops():
    with pytest.raises(sandbox.SandboxFailed, match="absolute"):
        agent_pi.check_stored_login(Path("rel/auth.json"))


@pytest.mark.parametrize("name", ["auth.json", "models.json"])
@pytest.mark.parametrize("kind", ["fifo", "fifo_link", "dir", "socket", "device_link"])
def test_a_non_regular_auth_json_stops_without_blocking(tmp_path, kind, name):
    tmp = Path(tempfile.mkdtemp(dir="/tmp"))
    fifo = tmp / "fifo"
    sock = None
    path = tmp / name
    try:
        if kind == "fifo":
            os.mkfifo(path)
            fifo = path
        elif kind == "fifo_link":
            os.mkfifo(fifo)
            os.symlink(fifo, path)
        elif kind == "dir":
            path.mkdir()
        elif kind == "socket":
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(str(path))
        else:
            os.symlink("/dev/null", path)
        outcome = []

        def run():
            try:
                agent_pi.check_stored_login(tmp / "auth.json")
                outcome.append(None)
            except BaseException as e:      # noqa: BLE001 - reported below
                outcome.append(e)

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(timeout=5)
        finished = not worker.is_alive()
        if not finished and kind in ("fifo", "fifo_link"):
            try:
                os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))
            except OSError:
                pass
        assert finished, "check_stored_login blocked"
        assert isinstance(outcome[0], sandbox.SandboxFailed)
        assert str(path) in str(outcome[0])
    finally:
        if sock:
            sock.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_symlinked_auth_json_is_read_like_pi_reads_it(tmp_path):
    real = tmp_path / "real.json"
    link = tmp_path / "auth.json"
    link.symlink_to(real)
    agent_pi.check_stored_login(link)                       # dangling: passes
    real.write_text('{"openai": {"access": "SECRET"}}')
    with pytest.raises(sandbox.SandboxFailed) as stop:
        agent_pi.check_stored_login(link)
    assert "SECRET" not in str(stop.value)
    real.write_text('{"anthropic": {}}')
    agent_pi.check_stored_login(link)


def _write_models(tmp_path, openai):
    (tmp_path / "models.json").write_text(json.dumps({"providers": {"openai": openai}}))


@pytest.mark.parametrize("body, field", [
    ({"apiKey": "SECRET"}, "providers.openai.apiKey"),
    ({"apiKey": "$SECRET_VAR"}, "providers.openai.apiKey"),
    ({"apiKey": "!echo SECRET"}, "providers.openai.apiKey"),
    ({"headers": {"Authorization": "Bearer SECRET"}}, "providers.openai.headers"),
    ({"models": [{"id": "m"}, {"id": "n", "headers": {"api-key": "SECRET"}}]},
     "providers.openai.models[1].headers"),
    ({"modelOverrides": {"gpt-SECRET": {"headers": {"x": "SECRET"}}}},
     "providers.openai.modelOverrides.<key>.headers"),
])
def test_a_models_json_credential_stops(tmp_path, body, field):
    _write_models(tmp_path, body)
    with pytest.raises(sandbox.SandboxFailed) as stop:
        agent_pi.check_stored_login(tmp_path / "auth.json")
    text = str(stop.value)
    assert str(tmp_path / "models.json") in text and field in text and "SECRET" not in text


def test_a_models_json_without_an_openai_credential_passes(tmp_path):
    auth = tmp_path / "auth.json"
    agent_pi.check_stored_login(auth)
    (tmp_path / "models.json").write_text(json.dumps({"providers": {
        "openai": {"baseUrl": "https://x", "authHeader": True, "headers": {}, "compat": {},
                   "models": [{"id": "m", "contextWindow": 1000, "reasoning": True}],
                   "modelOverrides": {"gpt-5": {"maxTokens": 10}}},
        "anthropic": {"apiKey": "k", "headers": {"a": "b"}}}}))
    agent_pi.check_stored_login(auth)
    (tmp_path / "models.json").write_text("{}")
    agent_pi.check_stored_login(auth)


def test_models_json_is_parsed_like_pi(tmp_path):
    auth = tmp_path / "auth.json"
    models = tmp_path / "models.json"
    models.write_text('﻿{ // note\n "providers": {"openai": {"apiKey": "SECRET",},},}')
    with pytest.raises(sandbox.SandboxFailed, match=r"providers\.openai\.apiKey") as stop:
        agent_pi.check_stored_login(auth)
    assert "SECRET" not in str(stop.value)
    models.write_text('{"providers": {"openai": {"baseUrl": "https://x//y"}}}')
    agent_pi.check_stored_login(auth)
    models.write_text('{ // only a comment\n "providers": {"openai": {"name": "x"},},\n}')
    agent_pi.check_stored_login(auth)


@pytest.mark.parametrize("content", [
    "not json", '{"providers": []}', '{"providers": {"openai": "x"}}',
    '{"providers": {"openai": {"models": "x"}}}'])
def test_an_unreadable_models_json_stops(tmp_path, content):
    (tmp_path / "models.json").write_text(content)
    with pytest.raises(sandbox.SandboxFailed, match="can't read") as stop:
        agent_pi.check_stored_login(tmp_path / "auth.json")
    assert str(tmp_path / "models.json") in str(stop.value)


def test_a_symlinked_models_json_is_read_like_pi_reads_it(tmp_path):
    real = tmp_path / "real.json"
    (tmp_path / "models.json").symlink_to(real)
    agent_pi.check_stored_login(tmp_path / "auth.json")     # dangling: passes
    real.write_text('{"providers": {"openai": {"apiKey": "SECRET"}}}')
    with pytest.raises(sandbox.SandboxFailed) as stop:
        agent_pi.check_stored_login(tmp_path / "auth.json")
    assert "SECRET" not in str(stop.value)


def test_auth_json_is_checked_before_models_json(tmp_path):
    (tmp_path / "auth.json").write_text('{"openai": {}}')
    _write_models(tmp_path, {"apiKey": "SECRET"})
    with pytest.raises(sandbox.SandboxFailed, match="openai entry"):
        agent_pi.check_stored_login(tmp_path / "auth.json")


@pytest.mark.parametrize("fault", ["error", "login", "models_key"])
def test_run_stops_before_pi_starts(broker, tmp_path, monkeypatch, fault):
    request = _fake_pi(tmp_path, monkeypatch, _turn("stop", "hi"))
    started = []
    monkeypatch.setattr(agent_pi.sandbox, "current", lambda: sandbox.LOCAL)
    monkeypatch.setattr(sandbox.LOCAL, "start", lambda *a, **k: started.append(1), raising=False)
    if fault == "error":
        broker["answer"] = b"ERR nope\n"
    elif fault == "models_key":
        _write_models(tmp_path, {"apiKey": "SECRET"})
    else:
        (tmp_path / "auth.json").write_text('{"openai": {}}')
    with pytest.raises(sandbox.SandboxFailed):
        agent_pi.run(request)
    assert started == []


# --- the roster's model check signs in through the broker too ---

@pytest.fixture
def catalog():
    agent_pi._pi_catalog.cache_clear()
    yield
    agent_pi._pi_catalog.cache_clear()


def _shipped(tmp_path, monkeypatch, roster="sssf.config.yaml"):
    """The shipped roster, with a fake pi that lists openai models only when it
    holds OPENAI_API_KEY, and every prompt file present."""
    cfg = agents.load_config(str(SKILL / "templates" / roster))
    monkeypatch.chdir(tmp_path)
    rows = []
    for a in cfg.agents:
        for ref in (a.prompt_engineering.system, a.prompt_engineering.user):
            f = tmp_path / ref
            f.parent.mkdir(parents=True, exist_ok=True)
            f.touch()
        if a.coding_agent != "claude_code":
            provider, model_id = a.model.split("/", 1)
            rows.append((provider, model_id))
    lines = "\n".join(
        (f'[ -n "$OPENAI_API_KEY" ] && echo "{p} {m} 200K"' if p == "openai"
         else f'echo "{p} {m} 200K"') for p, m in sorted(set(rows)))
    script = tmp_path / "pi"
    script.write_text(
        '#!/bin/sh\n'
        f'touch "{tmp_path}/ran"\n'
        f'printf %s "$OPENAI_API_KEY" > "{tmp_path}/seen_key"\n'
        'echo "provider model context"\n' + lines + "\n")
    script.chmod(0o755)
    monkeypatch.setattr(agent_pi, "PI_PATH", str(script))
    return cfg, [a.name for a in cfg.agents]


def test_the_model_check_signs_in_through_the_broker(broker, catalog, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg, names = _shipped(tmp_path, monkeypatch)
    agents.validate(cfg, names)
    assert (tmp_path / "seen_key").read_text() == "eyJ-tok"
    assert "OPENAI_API_KEY" not in os.environ
    assert "eyJ-tok" not in repr(agent_pi._pi_catalog())


def test_a_broker_error_stops_the_model_check_with_its_message(broker, catalog, tmp_path, monkeypatch):
    broker["answer"] = b"ERR sign in with pi, then /login\n"
    cfg, names = _shipped(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as stop:
        agents.validate(cfg, names)
    text = str(stop.value)
    assert text.startswith("config validation failed:")
    assert "sign in with pi, then /login" in text
    assert "not found in pi --list-models" not in text
    assert not (tmp_path / "ran").exists()


def test_a_stored_login_stops_the_model_check(broker, catalog, tmp_path, monkeypatch):
    (tmp_path / "auth.json").write_text('{"openai": {"access": "SECRET"}}')
    cfg, names = _shipped(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as stop:
        agents.validate(cfg, names)
    assert "openai entry" in str(stop.value) and "SECRET" not in str(stop.value)
    assert not (tmp_path / "ran").exists()


def test_a_models_json_key_stops_the_model_check(broker, catalog, tmp_path, monkeypatch):
    _write_models(tmp_path, {"apiKey": "SECRET"})
    cfg, names = _shipped(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as stop:
        agents.validate(cfg, names)
    assert "providers.openai.apiKey" in str(stop.value) and "SECRET" not in str(stop.value)
    assert not (tmp_path / "ran").exists()


def test_a_host_run_ignores_models_json(broker, tmp_path, monkeypatch):
    _write_models(tmp_path, {"apiKey": "SECRET"})
    assert agent_pi._in_container({"A": "1"}, object()) == {"A": "1"}      # a VM tree
    monkeypatch.delenv("SSSF_PI_TOKEN_SOCKET")
    assert agent_pi._in_container({"A": "1"}, sandbox.LOCAL) == {"A": "1"}


def test_a_claude_code_roster_ignores_models_json(broker, catalog, tmp_path, monkeypatch):
    _write_models(tmp_path, {"apiKey": "SECRET"})
    cfg, names = _shipped(tmp_path, monkeypatch, "sssf.config.claude_code.yaml")
    agents.validate(cfg, names)
    assert not (tmp_path / "ran").exists()


def test_an_api_key_from_the_broker_stops_the_model_check_without_echoing_it(
        broker, catalog, tmp_path, monkeypatch):
    broker["answer"] = b"OK sk-leak\n"
    cfg, names = _shipped(tmp_path, monkeypatch)
    with pytest.raises(SystemExit) as stop:
        agents.validate(cfg, names)
    assert "sk-leak" not in str(stop.value)


def test_a_host_model_check_is_unchanged(catalog, tmp_path, monkeypatch):
    monkeypatch.delenv("SSSF_PI_TOKEN_SOCKET", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg, names = _shipped(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="not found in pi --list-models"):
        agents.validate(cfg, names)
    agent_pi._pi_catalog.cache_clear()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-op")
    agents.validate(cfg, names)
    assert (tmp_path / "seen_key").read_text() == "sk-op"


def test_a_claude_code_roster_never_asks_the_broker(broker, catalog, tmp_path, monkeypatch):
    broker["answer"] = b"ERR nope\n"
    cfg, names = _shipped(tmp_path, monkeypatch, "sssf.config.claude_code.yaml")
    agents.validate(cfg, names)
    assert not (tmp_path / "ran").exists()
