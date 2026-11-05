"""container/pi_token_broker.py: hands the container a ChatGPT token from the host's pi.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

BROKER = Path(__file__).resolve().parent.parent / "templates/container/pi_token_broker.py"


@pytest.fixture
def box():
    d = Path(tempfile.mkdtemp(dir="/tmp"))
    procs = []

    def fake_pi(body):
        script = d / "pi"
        script.write_text(f"#!/bin/sh\necho \"$@\" > {d}/argv\n{body}\n")
        script.chmod(0o755)
        return str(script)

    def start(pi, sock=None):
        sock = sock or d / "b.sock"
        p = subprocess.Popen([sys.executable, str(BROKER), str(sock)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env={**os.environ, "PI_PATH": pi})
        procs.append(p)
        for _ in range(100):
            if Path(sock).exists() or p.poll() is not None:
                break
            time.sleep(0.05)
        return p, sock

    box = type("Box", (), {})()
    box.dir, box.fake_pi, box.start, box.procs = d, staticmethod(fake_pi), staticmethod(start), procs
    yield box
    for p in procs:
        if p.poll() is None:
            p.terminate()
        p.communicate()
    shutil.rmtree(d, ignore_errors=True)


def ask(sock) -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as c:
        c.settimeout(10)
        c.connect(str(sock))
        data = b""
        while chunk := c.recv(4096):
            data += chunk
    return data.decode()


def test_it_hands_out_the_token_and_asks_pi_the_fixed_way(box):
    _, sock = box.start(box.fake_pi("echo eyJ-access"))
    assert oct(os.stat(sock).st_mode & 0o777) == "0o600"
    assert ask(sock) == "OK eyJ-access\n"
    assert (box.dir / "argv").read_text().split() == (
        "auth print-bearer-token --provider openai --min-expiry 35m".split())


def test_an_api_key_is_refused_and_never_echoed(box):
    p, sock = box.start(box.fake_pi("echo sk-live"))
    reply = ask(sock)
    assert reply.startswith("ERR ") and "API key" in reply and "sk-live" not in reply
    p.terminate()
    out, err = p.communicate()
    assert "sk-live" not in out + err


def test_no_sign_in_is_an_error(box):
    _, sock = box.start(box.fake_pi("exit 1"))
    assert "Sign in with `pi`" in ask(sock) and ask(sock).startswith("ERR ")


def test_a_missing_pi_is_an_error(box):
    _, sock = box.start(str(box.dir / "no-such-pi"))
    assert "not installed" in ask(sock)


def test_the_token_is_never_printed(box):
    p, sock = box.start(box.fake_pi("echo eyJ-secret"))
    ask(sock)
    p.terminate()
    out, err = p.communicate()
    assert "eyJ-secret" not in out + err


def test_a_second_broker_leaves_a_live_socket_alone(box):
    pi = box.fake_pi("echo eyJ-access")
    _, sock = box.start(pi)
    second, _ = box.start(pi, sock)
    assert second.wait(timeout=10) == 1
    assert "another pi token broker" in second.communicate()[1]
    assert ask(sock) == "OK eyJ-access\n"


def test_a_stale_socket_is_replaced(box):
    sock = box.dir / "b.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(sock))
    s.close()                                   # a socket file, nothing listening
    _, sock = box.start(box.fake_pi("echo eyJ-access"), sock)
    time.sleep(0.2)
    assert ask(sock) == "OK eyJ-access\n"


def test_a_socket_left_by_a_terminated_broker_is_replaced(box):
    pi = box.fake_pi("echo eyJ-access")
    first, sock = box.start(pi)
    first.terminate()
    first.wait()
    assert Path(sock).exists()
    box.start(pi, sock)
    time.sleep(0.2)
    assert ask(sock) == "OK eyJ-access\n"
