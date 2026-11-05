"""`install.py --container` adds the operator container, and only on request.

On the host, Claude Code's auto mode blocks the command that launches a chain.
In sssf-sandbox, an operator session in a Podman container ran with
--dangerously-skip-permissions and finished run 1d56f792 with no prompt.
container/ packages that setup. It stays out of every repo that did not ask
for it, and an update keeps it current in every repo that did.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
STAMP = Path("adws/adw_sssf_config/stamp.json")
FILES = ("container/Containerfile", "container/build.sh", "container/run.sh",
         "container/pi_token_broker.py")


def install(*args: str, ok: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run([sys.executable, str(SKILL / "scripts" / "install.py"), *args],
                            capture_output=True, text=True, check=False)
    assert (result.returncode == 0) == ok, result.stdout + result.stderr
    return result


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_a_plain_install_has_no_container(repo):
    install("--coding-agent", "claude_code")
    assert not Path("container").exists()
    install("--update")
    assert not Path("container").exists()


def test_container_is_stamped_and_runnable(repo):
    out = install("--coding-agent", "claude_code", "--container").stdout
    for rel in FILES:
        assert Path(rel).is_file(), rel
    assert all(os.access(f"container/{n}", os.X_OK)
               for n in ("run.sh", "build.sh", "pi_token_broker.py"))
    assert set(FILES) <= set(json.loads(STAMP.read_text())["files"])
    assert "container/run.sh" in out


def test_container_can_be_added_to_an_installed_repo(repo):
    install("--coding-agent", "claude_code")
    install("--container")
    assert all(Path(rel).is_file() for rel in FILES)


def test_an_update_keeps_the_container_current(repo):
    install("--coding-agent", "claude_code", "--container")
    run = Path("container/run.sh")
    run.write_text(run.read_text() + "\n# older template\n")
    record = json.loads(STAMP.read_text())
    import hashlib
    record["files"]["container/run.sh"]["sha256"] = hashlib.sha256(run.read_bytes()).hexdigest()
    STAMP.write_text(json.dumps(record))
    install("--update")
    assert run.read_text() == (SKILL / "templates/container/run.sh").read_text()


def test_a_pi_roster_can_have_the_container(repo):
    out = install("--container").stdout
    assert all(Path(rel).is_file() for rel in FILES)
    assert "signed in to pi" in out


TEMPLATES = SKILL / "templates/container"


def test_the_image_pins_node_and_pi():
    text = (TEMPLATES / "Containerfile").read_text()
    assert "PI_VERSION=1.4.0" in text and "NODE_VERSION=24." in text
    assert "@earendil-works/pi-coding-agent@${PI_VERSION}" in text


def test_the_scripts_rebuild_on_a_changed_containerfile_and_run_the_broker():
    assert "sssf.containerfile.sha256" in (TEMPLATES / "build.sh").read_text()
    run = (TEMPLATES / "run.sh").read_text()
    for needle in ("pi_token_broker.py", "trap 'kill \"$broker\" 2>/dev/null' EXIT",
                   "/run/sssf/pi.sock", "SSSF_PI_TOKEN_SOCKET=/run/sssf/pi.sock",
                   "pasta:-T,43190", "sssf.containerfile.sha256",
                   '[ -S "$sock" ] && [ ! -L "$sock" ]', "s.connect(sys.argv[1])"):
        assert needle in run, needle
    for banned in ("[ -S \"$sock\" ] && break", "exec podman run", "OBS_DISABLE"):
        assert banned not in run, banned
    for script in ("run.sh", "build.sh"):
        subprocess.run(["bash", "-n", str(TEMPLATES / script)], check=True)


def _session(tmp_path, broker_source, at_sock):
    """Run a copy of run.sh with a stub podman. Returns (result, podman log lines, sock)."""
    rt = tempfile.mkdtemp(dir="/tmp")
    try:
        box = tmp_path / "box"
        subprocess.run(["git", "init", "-q", str(box)], check=True)
        for key, val in (("user.name", "t"), ("user.email", "t@t")):
            subprocess.run(["git", "-C", str(box), "config", key, val], check=True)
        cdir = box / "container"
        cdir.mkdir()
        shutil.copy(TEMPLATES / "run.sh", cdir / "run.sh")
        (cdir / "run.sh").chmod(0o755)
        (cdir / "Containerfile").write_text("FROM scratch\n")
        (cdir / "build.sh").write_text("#!/bin/sh\nexit 0\n")
        (cdir / "build.sh").chmod(0o755)
        (cdir / "pi_token_broker.py").write_text(broker_source)
        bindir = tmp_path / "bin"
        bindir.mkdir()
        (bindir / "podman").write_text('#!/bin/sh\necho "$*" >> "$PODMAN_LOG"\nexit 0\n')
        (bindir / "podman").chmod(0o755)
        token = tmp_path / "token.env"
        token.write_text("CLAUDE_CODE_OAUTH_TOKEN=x\n")
        log = tmp_path / "podman.log"
        sock = f"{rt}/sssf-pi-box.sock"
        at_sock(sock)
        env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "XDG_RUNTIME_DIR": rt,
               "SSSF_CONTAINER_TOKEN": str(token), "PODMAN_LOG": str(log), "PI_PATH": "false",
               "HOME": str(tmp_path)}
        done = subprocess.run(["bash", str(cdir / "run.sh")], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=60, env=env)
        lines = log.read_text().splitlines() if log.exists() else []
        # Look at the socket path before the temp dir goes.
        state = os.lstat(sock).st_mode if os.path.lexists(sock) else None
        kept = Path(sock).read_text() if state is not None and stat.S_ISREG(state) else None
        return done, lines, sock, state, kept
    finally:
        shutil.rmtree(rt, ignore_errors=True)


def _stale_socket(sock):
    import socket
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(sock)
    s.close()


def test_a_stale_socket_and_a_dead_broker_stop_the_session(tmp_path):
    done, lines, *_ = _session(tmp_path, "import time; time.sleep(60)\n", _stale_socket)
    assert done.returncode == 1
    assert "didn't answer" in done.stderr
    assert not [x for x in lines if x.startswith("run")]


def test_a_stale_socket_is_replaced_by_a_live_broker(tmp_path):
    done, lines, sock, state, _ = _session(
        tmp_path, (TEMPLATES / "pi_token_broker.py").read_text(), _stale_socket)
    assert done.returncode == 0, done.stderr
    runs = [x for x in lines if x.startswith("run")]
    assert runs and any("-it" in x for x in runs)
    assert state is not None and stat.S_ISSOCK(state)


def test_a_regular_file_at_the_socket_path_is_kept(tmp_path):
    done, lines, sock, state, kept = _session(
        tmp_path, (TEMPLATES / "pi_token_broker.py").read_text(),
        lambda s: Path(s).write_text("keep"))
    assert done.returncode == 1
    assert not [x for x in lines if x.startswith("run")]
    assert state is not None and stat.S_ISREG(state) and kept == "keep"


def test_adding_it_to_an_installed_repo_prints_only_its_own_steps(repo):
    install("--coding-agent", "claude_code")
    out = install("--container").stdout
    assert "container only" in out and "container/run.sh" in out
    assert "just demo" not in out and ".env.sample" not in out


def test_the_image_can_build_c_extensions_and_knows_time_zones():
    text = (SKILL / "templates/container/Containerfile").read_text()
    for package in ("build-essential", "python3-dev", "tzdata"):
        assert package in text, package
