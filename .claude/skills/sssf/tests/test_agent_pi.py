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
import pwd
import re
import shutil
import socket
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agent_pi, agents, obs_relay, sandbox
from adw_modules.data_types import AgentRequest, LocalGuard

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
                        raw_output_path=str(tmp_path / "raw.jsonl"), data_root=str(tmp_path), cwd=str(tmp_path),
                        local_guard=LocalGuard(mode="none"))


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

def _stop(server: socket.socket, thread: threading.Thread) -> None:
    """Wake a listening socket's accept loop, wait for it to end, then close the socket."""
    try:
        server.shutdown(socket.SHUT_RDWR)       # a blocked accept() returns EINVAL
    except OSError:
        pass
    thread.join(timeout=5)
    server.close()
    assert not thread.is_alive(), "the fake broker's accept loop didn't stop"


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
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setenv("SSSF_PI_TOKEN_SOCKET", path)
    monkeypatch.setattr(agent_pi, "PI_AUTH_JSON", tmp_path / "auth.json")
    yield state
    # Stop the thread before closing: a thread left in its accept loop can take
    # the fd number a later test's socket reuses and answer that test's client.
    _stop(server, thread)
    import shutil
    shutil.rmtree(d, ignore_errors=True)


# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
def test_the_container_gets_the_brokers_token(broker):
    env = agent_pi._in_container({"OBS_AUTH_TOKEN": "obs"}, sandbox.LOCAL)
    assert env["OPENAI_API_KEY"] == "eyJ-tok"
    assert env["OBS_AUTH_TOKEN"] == "obs" and "OBS_DISABLE" not in env


# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
def test_a_subscription_run_still_gets_the_brokers_token(broker, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-host")
    monkeypatch.setenv("SSSF_PI_AUTH", "subscription")
    env = agent_pi._in_container(agent_pi.child_env(), sandbox.LOCAL)
    assert env["OPENAI_API_KEY"] == "eyJ-tok"


# host_only, downstream: under agentsh it fails with: AssertionError: Regex pattern did not match.
@pytest.mark.host_only
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


# The expected values below are pi 1.4.0's real output: join(getAgentDir(), "auth.json")
# run under node v24.11.1 with HOME=/h ("<h>" stands for HOME here).
@pytest.mark.parametrize("value, expected", [
    (None, "<h>/.pi/agent/auth.json"),
    ("", "<h>/.pi/agent/auth.json"),
    ("~", "<h>/auth.json"),
    ("~/", "<h>/auth.json"),
    ("~/y", "<h>/y/auth.json"),
    ("~/a/../b", "<h>/b/auth.json"),
    ("/a/./b//c/", "/a/b/c/auth.json"),
    ("/..", "/auth.json"),
    ("file:///a/b", "/a/b/auth.json"),
    ("file:///a/b/../c", "/a/c/auth.json"),
    ("file:///", "/auth.json"),
])
def test_the_agent_dir_is_worked_out_as_pi_does(tmp_path, monkeypatch, value, expected):
    monkeypatch.setenv("HOME", str(tmp_path))
    if value is None:
        monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    else:
        monkeypatch.setenv("PI_CODING_AGENT_DIR", value)
    assert str(agent_pi._pi_auth_json()) == expected.replace("<h>", str(tmp_path))


def test_a_home_ending_in_a_slash_doubles_nothing(monkeypatch):
    monkeypatch.setenv("HOME", "/h/")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "~/y")
    assert str(agent_pi._pi_auth_json()) == "/h/y/auth.json"


@pytest.mark.parametrize("value, home, shown", [
    ("~root/x", "/h", "~root/x/auth.json"),
    ("~nosuchuser/x", "/h", "~nosuchuser/x/auth.json"),
    ("rel/x", "/h", "rel/x/auth.json"),
    ("file:/a", "/h", "file:/a/auth.json"),
    ("FILE:///a", "/h", "FILE:/a/auth.json"),
    ("./x", "/h", "x/auth.json"),
    ("~", "", "auth.json"),
    (None, "", ".pi/agent/auth.json"),
])
def test_a_relative_agent_dir_stops_with_todays_message(tmp_path, monkeypatch, value, home, shown):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "~root/x").mkdir(parents=True)
    (tmp_path / "~root/x/auth.json").write_text('{"openai": {}}')
    monkeypatch.setattr(agent_pi, "PI_AUTH_JSON", None)
    monkeypatch.setenv("HOME", home)
    if value is None:
        monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    else:
        monkeypatch.setenv("PI_CODING_AGENT_DIR", value)
    with pytest.raises(sandbox.SandboxFailed, match="a relative path") as stop:
        agent_pi.check_stored_login()
    assert f"at {shown}," in str(stop.value)


@pytest.mark.parametrize("value", [
    "file://localhost/x", "file://host/x", "file:///a%20b", "file:///a?q#f", "file:////a",
    "file:///a//../b", "file:///C:/../x", "file://", "file:///a\nb", "file:///a b",
])
def test_a_file_url_sssf_cant_match_stops(monkeypatch, value):
    monkeypatch.setattr(agent_pi, "PI_AUTH_JSON", None)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", value)
    with pytest.raises(sandbox.SandboxFailed, match="file:// URL"):
        agent_pi.check_stored_login()


@pytest.mark.parametrize("name, body", [
    ("auth.json", '{"openai": {"access": "x"}}'),
    ("models.json", '{"providers": {"openai": {"apiKey": "sk-x"}}}'),
])
def test_a_symlink_before_dotdot_is_read_as_pi_reads_it(tmp_path, monkeypatch, name, body):
    for d in ("real/b", "other/b", "other/sub", "real"):
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    os.symlink(tmp_path / "other/sub", tmp_path / "real/link")
    monkeypatch.setattr(agent_pi, "PI_AUTH_JSON", None)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", f"{tmp_path}/real/link/../b")
    # pi folds the text to real/b; the kernel would go to other/b
    (tmp_path / "real/b" / name).write_text(body)
    with pytest.raises(sandbox.SandboxFailed) as stop:
        agent_pi.check_stored_login()
    assert str(tmp_path / "real/b" / name) in str(stop.value)
    # roles swapped: the login sits where only the kernel would look
    (tmp_path / "real/b" / name).unlink()
    (tmp_path / "other/b" / name).write_text(body)
    agent_pi.check_stored_login()


def test_a_file_url_agent_dir_is_checked(tmp_path, monkeypatch):
    assert re.fullmatch(f"/(?:{agent_pi._URL_SEGMENT}/?)*", str(tmp_path))
    (tmp_path / "d").mkdir()
    (tmp_path / "d/auth.json").write_text('{"openai": {}}')
    monkeypatch.setattr(agent_pi, "PI_AUTH_JSON", None)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", f"file://{tmp_path}/d")
    with pytest.raises(sandbox.SandboxFailed) as stop:
        agent_pi.check_stored_login()
    assert str(stop.value).startswith(f"{tmp_path}/d/auth.json has")


@pytest.mark.parametrize("var", ["PI_CODING_AGENT_DIR", "HOME"])
def test_bad_utf8_stops(monkeypatch, var):
    monkeypatch.setattr(agent_pi, "PI_AUTH_JSON", None)
    if var == "HOME":
        monkeypatch.delenv("PI_CODING_AGENT_DIR", raising=False)
    monkeypatch.setenv(var, "/tmp/\udcff")
    with pytest.raises(sandbox.SandboxFailed, match="UTF-8"):
        agent_pi.check_stored_login()


def test_no_home_uses_the_passwd_entry(monkeypatch):
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "~/z")
    home = pwd.getpwuid(os.geteuid()).pw_dir
    assert agent_pi._pi_auth_json() == Path(agent_pi._node_join(home, "z/auth.json"))
    def nobody(uid):
        raise KeyError(uid)
    monkeypatch.setattr(agent_pi.pwd, "getpwuid", nobody)
    with pytest.raises(sandbox.SandboxFailed, match="HOME"):
        agent_pi._pi_auth_json()


def test_the_agent_dir_is_read_per_call(tmp_path, monkeypatch):
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    (tmp_path / "B/auth.json").write_text('{"openai": {}}')
    monkeypatch.setattr(agent_pi, "PI_AUTH_JSON", None)
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "A"))
    agent_pi.check_stored_login()
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(tmp_path / "B"))
    with pytest.raises(sandbox.SandboxFailed) as stop:
        agent_pi.check_stored_login()
    assert str(tmp_path / "B/auth.json") in str(stop.value)


def test_a_host_run_never_works_out_the_agent_dir(monkeypatch):
    monkeypatch.setenv("PI_CODING_AGENT_DIR", "file://host/x")
    monkeypatch.delenv("SSSF_PI_TOKEN_SOCKET", raising=False)
    assert agent_pi._in_container({"A": "1"}, sandbox.LOCAL) == {"A": "1"}
    monkeypatch.setenv("SSSF_PI_TOKEN_SOCKET", "/nonexistent.sock")
    assert agent_pi._in_container({"A": "1"}, object()) == {"A": "1"}


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


# host_only, downstream: under agentsh it fails with: SystemExit: config validation failed:
@pytest.mark.host_only
def test_the_model_check_signs_in_through_the_broker(broker, catalog, tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg, names = _shipped(tmp_path, monkeypatch)
    agents.validate(cfg, names)
    assert (tmp_path / "seen_key").read_text() == "eyJ-tok"
    assert "OPENAI_API_KEY" not in os.environ
    assert "eyJ-tok" not in repr(agent_pi._pi_catalog())


# host_only, the error names the sandbox: writes or links a file that agentsh denies.
@pytest.mark.host_only
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


# --- a pi agent in a VM: the obs relay ask (design decisions 40 and 46) --------

OBS_BASE = {"OBS_AUTH_TOKEN": "obs", "OBS_NAME": "builder", "OBS_POOL": "sssf-abc",
            "OBS_TAG": " sssf, ,adw-abc ,"}


def _vm_tree(monkeypatch):
    monkeypatch.setattr(agent_pi, "vm_token", lambda: "eyJ-access")
    asked = []
    return SimpleNamespace(install=lambda p: None, relay_obs=asked.append), asked


def test_a_vm_call_with_an_extension_and_the_obs_token_asks_for_a_relay(monkeypatch):
    vm, asked = _vm_tree(monkeypatch)
    env = agent_pi._in_vm(dict(OBS_BASE), vm, [], ["obs.ts"])
    [target] = asked
    assert (target.token, target.server, target.name, target.pool, target.tags) == \
        ("obs", obs_relay.DEFAULT_SERVER, "builder", "sssf-abc", ["sssf", "adw-abc"])
    assert env["OBS_DISABLE"] == "true" and "OBS_AUTH_TOKEN" not in env
    assert "obs" not in env.values() and "obs" not in repr(target)


@pytest.mark.parametrize("url,want", [("http://host:9", "http://host:9"), ("", obs_relay.DEFAULT_SERVER)])
def test_the_relay_posts_to_the_hosts_obs_server_url(monkeypatch, url, want):
    vm, asked = _vm_tree(monkeypatch)
    agent_pi._in_vm({**OBS_BASE, "OBS_SERVER_URL": url}, vm, [], ["obs.ts"])
    assert [t.server for t in asked] == [want]


@pytest.mark.parametrize("change,extensions", [
    ({}, []),
    ({}, [str(agent_pi.REFUSE_EXTENSION)]),
    ({"OBS_AUTH_TOKEN": None}, ["obs.ts"]),
    ({"OBS_AUTH_TOKEN": ""}, ["obs.ts"]),
    ({"OBS_DISABLE": "true"}, ["obs.ts"]),
])
def test_no_relay_is_asked_for_when_obs_couldnt_post(monkeypatch, change, extensions):
    vm, asked = _vm_tree(monkeypatch)
    env = {**OBS_BASE, **change}
    env = {k: v for k, v in env.items() if v is not None}
    out = agent_pi._in_vm(env, vm, [], extensions)
    assert asked == [] and out["OBS_DISABLE"] == "true" and "OBS_AUTH_TOKEN" not in out


def test_an_operator_obs_disable_other_than_true_still_relays(monkeypatch):
    vm, asked = _vm_tree(monkeypatch)
    env = agent_pi._in_vm({**OBS_BASE, "OBS_DISABLE": "1"}, vm, [], ["obs.ts"])
    assert len(asked) == 1 and env["OBS_DISABLE"] == "true"


def test_a_local_tree_never_asks_for_a_relay():
    env = agent_pi._in_vm(dict(OBS_BASE), sandbox.LOCAL, [], ["obs.ts"])
    assert env == OBS_BASE


def test_run_passes_the_calls_extensions_to_in_vm(tmp_path, monkeypatch):
    seen = []

    def spy(env, tree, refuse_rules, extensions=()):
        seen.append(extensions)
        return env
    req = _fake_pi(tmp_path, monkeypatch, _turn("stop", "hi")).model_copy(update={"extensions": ["a.ts"]})
    monkeypatch.setattr(agent_pi, "_in_vm", spy)
    monkeypatch.setattr(sandbox, "current", lambda: sandbox.LOCAL)
    try:
        agent_pi.run(req)
    except RuntimeError:
        pass
    assert seen == [["a.ts"]]


# ── a wrapped pi agent's own agent dir (local_guard: agentsh) ────────────────
# Every test here sets HOME and MODELS_JSON under tmp_path, so nothing is read or written in the
# real home, and no real pi runs.

from adw_modules import agentsh_guard  # noqa: E402

KEY = "sk-literal-models-key"


class Own:
    """A wrapped call with the wrap stubbed: `start` records what the agent would have seen."""

    def __init__(self, tmp_path, monkeypatch, provider="openai", adw_id="abc", guard="agentsh"):
        self.tmp, self.mp = tmp_path, monkeypatch
        self.home = tmp_path / "home"
        (self.home / ".pi" / "agent").mkdir(parents=True)
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.delenv("SSSF_PI_TOKEN_SOCKET", raising=False)
        monkeypatch.delenv("SSSF_PI_AUTH", raising=False)
        self.models = self.home / ".pi" / "agent" / "models.json"
        self.models.write_text('{"providers": {"x": {"apiKey": "%s"}}}' % KEY)
        monkeypatch.setattr(agent_pi, "MODELS_JSON", str(self.models))
        self.request = _fake_pi(tmp_path, monkeypatch, _turn("stop", text="ok"))
        monkeypatch.setattr(agent_pi, "resolve_model", lambda model: (provider, "gpt"))
        self.request.adw_id = adw_id
        data = tmp_path / "data"
        data.mkdir()
        self.request.data_root = str(data)
        self.request.session_dir = str(data / "sessions")
        self.request.raw_output_path = str(data / "raw.jsonl")
        self.request.local_guard = LocalGuard(mode=guard)
        self.dir = Path(agentsh_guard.pi_home(adw_id, str(self.home)))
        self.homes = self.home / agentsh_guard.PI_HOMES
        self.seen: list[dict] = []
        self.auth_calls = tmp_path / "auth_calls"
        self.token, self.code, self.check = "tok-123", 0, ""
        self._script()
        real_start = agentsh_guard.start

        def start(tree, harness, cmd, *, cwd, env, guard, tally=None, on_event=None):
            self.seen.append({"cmd": cmd, "env": dict(env),
                              "auth": self._read(self.dir / "auth.json"),
                              "models": self._read(self.dir / "models.json")})
            return real_start(tree, harness, cmd, cwd=cwd, env=env, guard=LocalGuard(mode="none"),
                              tally=tally, on_event=on_event)
        monkeypatch.setattr(agentsh_guard, "start", start)

    @staticmethod
    def _read(path):
        return path.read_bytes() if path.is_file() else None

    def _script(self):
        (self.tmp / "token").write_text(self.token)
        (self.tmp / "check").write_text(self.check)
        pi = Path(agent_pi.PI_PATH)
        events = self.tmp / "events.jsonl"
        pi.write_text(f"""#!/bin/sh
if [ "$1" = auth ]; then
  echo "$2 $4" >> {self.auth_calls}
  if [ "$2" = check ]; then cat {self.tmp}/check; exit 0; fi
  cat {self.tmp}/token
  exit {self.code}
fi
cat {events}
""")

    def set(self, token=None, code=None, check=None):
        self.token = self.token if token is None else token
        self.code = self.code if code is None else code
        self.check = self.check if check is None else check
        self._script()

    def run(self):
        return agent_pi.run(self.request)

    def auth(self):
        return self.auth_calls.read_text().splitlines() if self.auth_calls.exists() else []


@pytest.fixture
def own(tmp_path, monkeypatch):
    return Own(tmp_path, monkeypatch)


def test_the_host_call_has_discovery_off_and_the_operators_environment(tmp_path, monkeypatch):
    log = tmp_path / "argv"
    envlog = tmp_path / "env"
    script = tmp_path / "pi"
    script.write_text(f'#!/bin/sh\necho "$@" > {log}\nenv > {envlog}\n'
                      'echo "provider model context"\necho "openai gpt 200K"\n')
    script.chmod(0o755)
    monkeypatch.setattr(agent_pi, "PI_PATH", str(script))
    monkeypatch.delenv("SSSF_PI_TOKEN_SOCKET", raising=False)
    agent_pi._pi_catalog.cache_clear()
    try:
        assert agent_pi._pi_catalog() == [("openai", "gpt", 200_000)]
    finally:
        agent_pi._pi_catalog.cache_clear()
    argv = log.read_text().split()
    assert argv[0] == "--list-models" and set(agent_pi.HOST_FLAGS) <= set(argv)
    assert set(agent_pi.HOST_FLAGS) == {"--no-extensions", "--no-skills", "--no-prompt-templates",
                                        "--no-themes", "--no-context-files"}
    got = dict(line.split("=", 1) for line in envlog.read_text().splitlines() if "=" in line)
    for name, value in agent_pi.operator_env().items():
        if name not in ("_", "SHLVL", "PWD", "OLDPWD"):
            assert got.get(name) == value, name


def test_every_agent_call_carries_the_agent_flags_and_the_refuse_extension_last(tmp_path, monkeypatch):
    request = _fake_pi(tmp_path, monkeypatch, _turn("stop", text="ok"))
    request.extensions = ["a.ts"]
    seen = []
    real = agent_pi.agentsh_guard.start

    def start(tree, harness, cmd, **kw):
        seen.append(list(cmd))
        return real(tree, harness, cmd, **kw)
    monkeypatch.setattr(agent_pi.agentsh_guard, "start", start)
    agent_pi.run(request)
    cmd = seen[0]
    for flag in agent_pi.AGENT_FLAGS:
        assert flag in cmd, flag
    assert set(agent_pi.AGENT_FLAGS) == {"--no-extensions", "--no-prompt-templates", "--no-themes"}
    assert "--no-skills" not in cmd and "--no-context-files" not in cmd
    assert cmd[cmd.index("-e") + 1] == "a.ts"
    assert [cmd[i + 1] for i, c in enumerate(cmd) if c == "-e"][-1] == str(agent_pi.REFUSE_EXTENSION)
    assert cmd.index("--no-extensions") < cmd.index("-e")


def test_the_agent_gets_its_own_dir_outside_the_data_root(own):
    own.set(code=0)
    own.run()
    assert own.homes.stat().st_mode & 0o777 == 0o700          # SSSF made it
    (own.dir / "auth.json").write_text('{"openai": {"type": "oauth", "refresh": "R"}}')
    own.run()
    seen = own.seen[1]
    assert seen["env"]["PI_CODING_AGENT_DIR"] == agentsh_guard.pi_home("abc", str(own.home))
    assert not str(own.dir).startswith(own.request.data_root + os.sep)
    assert own.homes.stat().st_mode & 0o777 == 0o700
    assert seen["auth"] == b"{}"
    assert seen["models"] == own.models.read_bytes() and KEY.encode() in seen["models"]
    assert seen["env"]["PATH"].endswith(os.pathsep + str(own.models.parent / "bin"))
    assert seen["env"]["OPENAI_API_KEY"] == "tok-123"
    for path in Path(own.request.data_root).rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes(), path


def test_a_second_call_writes_both_files_again(own):
    own.run()
    own.models.write_text('{"providers": {}}')
    (own.dir / "auth.json").write_text('{"x": 1}')
    own.run()
    assert own.seen[1]["models"] == b'{"providers": {}}' and own.seen[1]["auth"] == b"{}"


def test_a_link_at_the_agent_dir_is_replaced_not_followed(own, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    own.homes.mkdir(parents=True)
    own.dir.symlink_to(outside)
    own.run()
    assert list(outside.iterdir()) == []
    assert not own.dir.is_symlink() and own.seen[0]["auth"] == b"{}"


@pytest.mark.parametrize("kind", ["link", "file"])
def test_a_link_or_file_at_the_homes_folder_stops_the_call(own, tmp_path, kind):
    own.homes.parent.mkdir(parents=True)
    if kind == "link":
        (tmp_path / "elsewhere").mkdir()
        own.homes.symlink_to(tmp_path / "elsewhere")
    else:
        own.homes.write_text("x")
    with pytest.raises(sandbox.SandboxFailed, match="sssf-pi"):
        own.run()
    assert own.seen == []
    if kind == "link":
        assert list((tmp_path / "elsewhere").iterdir()) == []


@pytest.mark.parametrize("bad", ["..", "a/b", ""])
def test_a_run_id_that_is_not_one_plain_name_stops_the_call(own, bad):
    own.request.adw_id = bad
    with pytest.raises(sandbox.SandboxFailed):
        own.run()
    assert own.seen == [] and not own.homes.exists()


def test_with_local_guard_none_pi_uses_its_own_folder_and_nothing_is_made(own):
    own.request.local_guard = LocalGuard(mode="none")
    own.run()
    assert "PI_CODING_AGENT_DIR" not in own.seen[0]["env"]
    assert own.auth() == [] and not own.homes.exists()


@pytest.mark.parametrize("target", ["auth.json", "models.json"])
@pytest.mark.parametrize("inside", [False, True])
def test_a_folder_where_a_file_goes_stops_the_call_and_stays(own, target, inside):
    own.dir.mkdir(parents=True)
    (own.dir / target).mkdir()
    if inside:
        (own.dir / target / "keep").write_text("x")
    with pytest.raises(sandbox.SandboxFailed) as raised:
        own.run()
    assert not isinstance(raised.value, IsADirectoryError)
    assert str(own.dir / target) in str(raised.value)
    assert own.seen == [] and (own.dir / target).is_dir()
    if inside:
        assert (own.dir / target / "keep").read_text() == "x"


def test_openai_gets_the_host_token_in_openai_api_key(own):
    own.run()
    assert own.seen[0]["env"]["OPENAI_API_KEY"] == "tok-123"
    assert own.auth() == ["print-bearer-token openai"]


def test_anthropic_token_goes_in_the_oauth_variable_and_an_api_key_in_the_key_variable(tmp_path, monkeypatch):
    own = Own(tmp_path, monkeypatch, provider="anthropic")
    own.run()
    assert own.seen[0]["env"]["ANTHROPIC_OAUTH_TOKEN"] == "tok-123"
    own.set(token="sk-ant-api-xyz")
    own.run()
    assert own.seen[1]["env"]["ANTHROPIC_API_KEY"] == "sk-ant-api-xyz"


def test_a_failed_auth_with_no_key_names_the_provider_and_writes_nothing(own, monkeypatch):
    for var in ("OPENAI_API_KEY",):
        monkeypatch.delenv(var, raising=False)
    own.set(token="", code=1)
    with pytest.raises(sandbox.SandboxFailed, match="openai"):
        own.run()
    assert own.seen == [] and not own.dir.exists()


def test_a_failed_auth_with_a_key_in_the_environment_goes_on(own, monkeypatch):
    own.set(token="", code=1)
    monkeypatch.setattr(agent_pi, "child_env", lambda *a, **k: {"OPENAI_API_KEY": "sk-env", "PATH": "/usr/bin"})
    own.run()
    assert own.seen[0]["env"]["OPENAI_API_KEY"] == "sk-env"


def test_an_api_key_under_subscription_mode_raises_without_the_key(own, monkeypatch):
    monkeypatch.setenv("SSSF_PI_AUTH", "subscription")
    own.set(token="sk-secret-key")
    with pytest.raises(sandbox.SandboxFailed) as raised:
        own.run()
    assert "sk-secret-key" not in str(raised.value) and own.seen == []


def test_another_providers_stored_sign_in_stops_and_a_key_or_failure_goes_on(tmp_path, monkeypatch):
    own = Own(tmp_path, monkeypatch, provider="google")
    own.set(check='{"status": "ready", "authType": "oauth"}')
    with pytest.raises(sandbox.SandboxFailed, match="google"):
        own.run()
    own.set(check='{"status": "ready", "authType": "api_key"}')
    own.run()
    own.set(check="not json")
    own.run()
    assert len(own.seen) == 2


def test_a_token_socket_makes_no_auth_call(own, monkeypatch):
    monkeypatch.setenv("SSSF_PI_TOKEN_SOCKET", str(own.tmp / "sock"))
    monkeypatch.setattr(agent_pi, "_in_container", lambda env, tree: env)
    own.run()
    assert own.auth() == []
