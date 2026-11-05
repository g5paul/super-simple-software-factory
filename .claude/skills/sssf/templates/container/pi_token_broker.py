#!/usr/bin/env python3
"""Hand the operator container a ChatGPT access token from the host's pi sign-in.

run.sh runs this on the host as `python3 pi_token_broker.py <socket path>` and
mounts the socket into the container. The refresh token never leaves the host.

Protocol: a client connects and sends nothing. The broker writes exactly one
line and closes:
    OK <token>\\n       on success
    ERR <message>\\n    on failure (one line, never a token)

Installs no signal handlers and doesn't unlink its socket on exit: run.sh's
trap only kills it, and the next start replaces a socket nothing answers on.
Never prints or logs a token. Stdlib only; works on Python 3.10.
"""

from __future__ import annotations

import os
import socket
import socketserver
import stat
import subprocess
import sys

MIN_EXPIRY = "35m"
PI = os.environ.get("PI_PATH", "pi")
PI_TIMEOUT = 120  # seconds


def mint() -> tuple[bool, str]:
    """(True, token) or (False, message). The message never has a token or pi's stderr."""
    try:
        done = subprocess.run([PI, "auth", "print-bearer-token", "--provider", "openai",
                               "--min-expiry", MIN_EXPIRY],
                              capture_output=True, text=True, timeout=PI_TIMEOUT, check=False)
    except FileNotFoundError:
        return False, ("pi is not installed on the host, so the container can't get a ChatGPT "
                       "token. Install pi on the host, sign in with `pi`, then /login.")
    except subprocess.TimeoutExpired:
        return False, f"pi auth print-bearer-token on the host took longer than {PI_TIMEOUT}s."
    token = done.stdout.strip()
    if done.returncode != 0 or not token:
        return False, ("pi has no ChatGPT sign-in on the host to hand the container a token from "
                       "(pi auth print-bearer-token --provider openai exited "
                       f"{done.returncode}). Sign in with `pi` on the host, then /login.")
    if token.startswith("sk-"):
        return False, ("pi's openai credential on the host is an API key, not the ChatGPT "
                       "sign-in, and SSSF doesn't put API keys in the container. Sign in to "
                       "ChatGPT with `pi` on the host and /login.")
    return True, token


class Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        ok, value = mint()
        line = ("OK " if ok else "ERR ") + value.replace("\n", " ") + "\n"
        try:
            self.request.sendall(line.encode())
        except (BrokenPipeError, ConnectionResetError):
            pass


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: pi_token_broker.py <socket path>", file=sys.stderr)
        return 2
    path = argv[0]
    if os.path.lexists(path):
        if not stat.S_ISSOCK(os.lstat(path).st_mode):
            print(f"{path} exists and is not a socket; not touching it", file=sys.stderr)
            return 1
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(2)
        try:
            probe.connect(path)
        except OSError:
            os.unlink(path)  # stale: nothing answers on it
        else:
            print(f"another pi token broker is listening at {path}; "
                  "is a container session for this repo already open?", file=sys.stderr)
            return 1
        finally:
            probe.close()
    os.umask(0o077)
    server = Server(path, Handler)
    os.chmod(path, 0o600)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
