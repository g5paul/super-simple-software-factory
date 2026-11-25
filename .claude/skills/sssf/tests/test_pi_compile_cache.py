"""Node's compile cache: pi's bin calls enableCompileCache(), and Node would read V8 code cache from
$TMPDIR/node-compile-cache, which any agent can write. Every pi SSSF starts carries
NODE_DISABLE_COMPILE_CACHE=1 (agent_pi.NO_COMPILE_CACHE), set last.

Every real node or pi here has TMPDIR under tmp_path, so the real /tmp/node-compile-cache is never
read or written. The stand-in pi scripts write their environment to a file."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))
from adw_modules import agent_pi, sandbox_remote, trial  # noqa: E402

PROBE = Path(__file__).resolve().parent / "fixtures" / "compile_cache" / "probe.mjs"
BROKER = SKILL / "templates" / "container" / "pi_token_broker.py"
SETTING = "NODE_DISABLE_COMPILE_CACHE=1"


def _broker():
    spec = importlib.util.spec_from_file_location("pi_token_broker_cc", BROKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_three_modules_name_the_same_setting():
    want = {"NODE_DISABLE_COMPILE_CACHE": "1"}
    assert agent_pi.NO_COMPILE_CACHE == sandbox_remote.PI_NO_COMPILE_CACHE == _broker().NO_COMPILE_CACHE == want
    assert agent_pi.NO_EXTENSION_CACHE == {"JITI_FS_CACHE": "false"}


# ── the real switch ──────────────────────────────────────────────────────────

def _node_env(tmp: Path, **extra: str) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if k == "PATH"}
    return {**base, "TMPDIR": str(tmp), **extra}


def _probe(tmp_path: Path, tmp: Path, **extra: str) -> dict:
    done = subprocess.run(["node", str(PROBE)], env=_node_env(tmp, **extra), capture_output=True,
                          text=True, timeout=60, cwd=str(tmp_path))
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@needs_node
def test_with_the_setting_node_reports_disabled_and_writes_nothing(tmp_path):
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    out = _probe(tmp_path, tmp, **agent_pi.NO_COMPILE_CACHE)
    assert out["result"]["status"] == out["status"]["DISABLED"]
    assert not (tmp / "node-compile-cache").exists()


@needs_node
def test_the_setting_wins_over_node_compile_cache(tmp_path):
    tmp, other = tmp_path / "tmp", tmp_path / "other"
    tmp.mkdir()
    out = _probe(tmp_path, tmp, NODE_COMPILE_CACHE=str(other), **agent_pi.NO_COMPILE_CACHE)
    assert out["result"]["status"] == out["status"]["DISABLED"]
    assert not (tmp / "node-compile-cache").exists() and not other.exists()


@needs_node
def test_without_the_setting_node_fills_the_cache(tmp_path):
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    out = _probe(tmp_path, tmp)
    assert out["result"]["status"] == out["status"]["ENABLED"]
    assert Path(out["result"]["directory"]).is_relative_to(tmp)
    assert any((tmp / "node-compile-cache").rglob("*"))


# ── the host's own starts ────────────────────────────────────────────────────

@pytest.fixture(params=["clean", "operator_set"])
def operator(request, monkeypatch):
    monkeypatch.delenv("SSSF_PI_TOKEN_SOCKET", raising=False)
    if request.param == "clean":
        monkeypatch.delenv("NODE_DISABLE_COMPILE_CACHE", raising=False)
        monkeypatch.delenv("NODE_COMPILE_CACHE", raising=False)
    else:
        monkeypatch.setenv("NODE_DISABLE_COMPILE_CACHE", "0")
        monkeypatch.setenv("NODE_COMPILE_CACHE", "/somewhere")
    return request.param


def _standin(tmp_path: Path, stdout: str) -> tuple[Path, Path]:
    envlog = tmp_path / "env"
    script = tmp_path / "pi"
    script.write_text(f"#!/bin/sh\nenv > {envlog}\n{stdout}\n")
    script.chmod(0o755)
    return script, envlog


def _lines(envlog: Path) -> list[str]:
    return envlog.read_text().splitlines()


def test_the_pi_catalog_has_it(tmp_path, monkeypatch, operator):
    script, envlog = _standin(tmp_path, 'echo "provider model context"\necho "openai gpt 200K"')
    monkeypatch.setattr(agent_pi, "PI_PATH", str(script))
    agent_pi._pi_catalog.cache_clear()
    try:
        agent_pi._pi_catalog()
    finally:
        agent_pi._pi_catalog.cache_clear()
    lines = _lines(envlog)
    assert SETTING in lines
    assert not any(l.startswith("JITI_FS_CACHE=") for l in lines)


@pytest.mark.parametrize("provider, check", [("openai", False), ("google", True)])
def test_the_host_token_has_it(tmp_path, monkeypatch, operator, provider, check):
    script, envlog = _standin(tmp_path, "echo tok")
    monkeypatch.setattr(agent_pi, "PI_PATH", str(script))
    agent_pi._host_token(provider, check=check)
    assert SETTING in _lines(envlog)


def test_the_trial_check_has_it(tmp_path, monkeypatch, operator):
    script, envlog = _standin(tmp_path, 'echo \'{"status": "ready", "authType": "api_key"}\'')
    monkeypatch.setattr(agent_pi, "PI_PATH", str(script))
    trial.pi_problems("google")
    assert SETTING in _lines(envlog)


def test_the_broker_mint_has_it(tmp_path, monkeypatch, operator):
    script, envlog = _standin(tmp_path, "echo tok")
    broker = _broker()
    monkeypatch.setattr(broker, "PI", str(script))
    assert broker.mint() == (True, "tok")
    assert SETTING in _lines(envlog)


# ── the cost ─────────────────────────────────────────────────────────────────

@pytest.mark.skipif(shutil.which("pi") is None, reason="pi is not installed")
def test_the_cost_on_a_real_pi(tmp_path):
    home, agent, tmp, tmp_off = (tmp_path / n for n in ("home", "agent", "tmp", "tmp_off"))
    for d in (home, agent, tmp, tmp_off):
        d.mkdir()
    base = {k: v for k, v in os.environ.items() if k == "PATH"}
    env = {**base, "HOME": str(home), "PI_CODING_AGENT_DIR": str(agent), "PI_OFFLINE": "1"}
    pi = shutil.which("pi")
    argv = [pi, "--list-models", *agent_pi.HOST_FLAGS, "--no-approve", "--offline"]

    def timed(where: Path, **extra: str) -> float:
        start = time.monotonic()
        done = subprocess.run(argv, env={**env, "TMPDIR": str(where), **extra}, cwd="/",
                              capture_output=True, timeout=120)
        assert done.returncode == 0, done.stderr
        return time.monotonic() - start

    timed(tmp)                                           # fills the cache
    warm = statistics.median(timed(tmp) for _ in range(5))
    off = statistics.median(timed(tmp_off, **agent_pi.NO_COMPILE_CACHE) for _ in range(5))
    assert not (tmp_off / "node-compile-cache").exists()
    print(f"warm cache median {warm:.2f}s, NODE_DISABLE_COMPILE_CACHE=1 median {off:.2f}s")
    assert off < warm + 2
