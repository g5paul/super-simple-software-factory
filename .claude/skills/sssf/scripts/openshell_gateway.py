# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Size the OpenShell gateway's VMs and restart it when that is safe.
`just openshell-setup` runs both steps.

    uv run <skill>/scripts/openshell_gateway.py size --config <gateway.toml>
    uv run <skill>/scripts/openshell_gateway.py restart --config <gateway.toml>

`size` writes vcpus, mem_mib and overlay_disk_mib under [openshell.drivers.vm] and
changes nothing else in the file. Each is the override if set, else the file's value
if it is a whole number of 1 or more, else the default (4, 4096, 16384). Overrides:
SSSF_OPENSHELL_VCPUS, SSSF_OPENSHELL_MEM_MIB, SSSF_OPENSHELL_OVERLAY_DISK_MIB. The
gateway's own `config preflight` must accept the new file before it replaces the old.

`restart` restarts the gateway only when gateway.toml is newer than the gateway's
start and `openshell sandbox list` shows no sandbox, since a restart stops them all.
It keeps no state.

Both refuse while SSSF_ADW_ID is set, as `just openshell-setup` does.
"""

from __future__ import annotations

import argparse
import calendar
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

TABLE = "openshell.drivers.vm"
DEFAULTS = {"vcpus": 4, "mem_mib": 4096, "overlay_disk_mib": 16384}
OVERRIDES = {"vcpus": "SSSF_OPENSHELL_VCPUS", "mem_mib": "SSSF_OPENSHELL_MEM_MIB",
             "overlay_disk_mib": "SSSF_OPENSHELL_OVERLAY_DISK_MIB"}
UNIT = "openshell-gateway"

HEADER = re.compile(r"^\s*\[\s*openshell\s*\.\s*drivers\s*\.\s*vm\s*\]\s*(#.*)?$")
START = re.compile(r"^\w{3} (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:\.\d+)? UTC$")
WHOLE = re.compile(r"[1-9][0-9]*", re.ASCII)


class Stop(Exception):
    """A message for stderr and exit 1."""


def last(text: str, n: int = 5) -> str:
    return "\n".join((text or "").strip().splitlines()[-n:])


def overrides() -> dict[str, int]:
    found = {}
    for key, name in OVERRIDES.items():
        value = os.environ.get(name, "")
        if value == "":
            continue
        if not WHOLE.fullmatch(value):
            raise Stop(f"{name} must be a whole number of 1 or more, not {value!r}")
        found[key] = int(value)
    return found


def good(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def table_of(doc: dict) -> dict:
    node = doc
    for part in TABLE.split("."):
        node = node.get(part) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else {}


def strip_sizes(doc: dict) -> dict:
    """The document without the three keys, and without a vm or drivers table left empty."""
    doc = {k: (dict(v) if isinstance(v, dict) else v) for k, v in doc.items()}
    root = doc.get("openshell")
    if isinstance(root, dict):
        root = doc["openshell"] = dict(root)
        drivers = root.get("drivers")
        if isinstance(drivers, dict):
            drivers = root["drivers"] = dict(drivers)
            vm = drivers.get("vm")
            if isinstance(vm, dict):
                vm = drivers["vm"] = {k: v for k, v in vm.items() if k not in DEFAULTS}
                if not vm:
                    del drivers["vm"]
            if not drivers:
                del root["drivers"]
    return doc


def edit(text: str, wanted: dict[str, int], present: dict) -> str:
    lines = text.splitlines(keepends=True)
    head = next((i for i, line in enumerate(lines) if HEADER.match(line.rstrip("\r\n"))), None)
    if head is None:
        out = text
        if out and not out.endswith("\n"):
            out += "\n"
        if out and not out.endswith("\n\n"):
            out += "\n"
        out += f"[{TABLE}]\n" + "".join(f"{k} = {wanted[k]}\n" for k in DEFAULTS)
        return out
    end = len(lines)
    for i in range(head + 1, len(lines)):
        if lines[i].strip().startswith("["):
            end = i
            break
    missing = []
    for key in DEFAULTS:
        pattern = re.compile(rf"^(\s*{key}\s*=\s*)([^#\r\n]*?)(\s*(?:#.*)?)(\r?\n?)$")
        for i in range(head + 1, end):
            m = pattern.match(lines[i])
            if m:
                if present.get(key) != wanted[key]:
                    lines[i] = f"{m.group(1)}{wanted[key]}{m.group(3)}{m.group(4)}"
                break
        else:
            missing.append(key)
    if lines[head].endswith("\n") is False:
        lines[head] += "\n"
    lines[head + 1:head + 1] = [f"{k} = {wanted[k]}\n" for k in missing]
    return "".join(lines)


def preflight(path: Path) -> None:
    try:
        done = subprocess.run(["openshell-gateway", "config", "preflight", "--path", str(path)],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as error:
        raise Stop(f"openshell-gateway config preflight didn't run: {error}")
    if done.returncode != 0:
        raise Stop("openshell-gateway config preflight refused the new gateway.toml; "
                   f"it is unchanged.\n{last(done.stderr or done.stdout)}")


def size(config: Path) -> int:
    given = overrides()
    try:
        old_text = config.read_bytes().decode("utf-8")
        old = tomllib.loads(old_text)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise Stop(f"can't read {config}: {error}")
    present = table_of(old)
    wanted, notes = {}, []
    for key, default in DEFAULTS.items():
        if key in given:
            wanted[key] = given[key]
        elif good(present.get(key)):
            wanted[key] = present[key]
        else:
            wanted[key] = default
            if key in present:
                notes.append(f"gateway.toml: {key} = {present[key]!r} isn't a whole number "
                             f"of 1 or more; using {default}")
    for note in notes:
        print(note)
    if all(good(present.get(k)) and present[k] == wanted[k] for k in DEFAULTS):
        print("gateway.toml: VM sizes unchanged (" +
              ", ".join(f"{k} {wanted[k]}" for k in DEFAULTS) + ")")
        return 0
    keep = {k: present[k] for k in DEFAULTS if good(present.get(k))}
    new_text = edit(old_text, wanted, keep)
    cannot = ("the file's layout isn't one setup can edit; set vcpus, mem_mib and "
              f"overlay_disk_mib under [{TABLE}] by hand")
    try:
        new = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError:
        raise Stop(cannot)
    got = table_of(new)
    if any(not good(got.get(k)) or got[k] != wanted[k] for k in DEFAULTS) \
            or strip_sizes(old) != strip_sizes(new):
        raise Stop(cannot)
    real = Path(os.path.realpath(config))
    handle, name = tempfile.mkstemp(dir=real.parent, prefix=".gateway.toml.")
    temp = Path(name)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(new_text.encode("utf-8"))
        os.chmod(temp, real.stat().st_mode & 0o7777)
        preflight(temp)
        os.replace(temp, real)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    for key in DEFAULTS:
        if key in keep and keep[key] != wanted[key]:
            print(f"gateway.toml: {key} {keep[key]} -> {wanted[key]}")
        elif key not in keep:
            print(f"gateway.toml: set {key} = {wanted[key]}")
    return 0


def run(argv: list[str], env: dict | None = None):
    return subprocess.run(argv, capture_output=True, text=True, timeout=30, env=env)


def restart(config: Path) -> int:
    env = {**os.environ, "TZ": "UTC", "LC_ALL": "C"}
    shown = ""
    start = None
    try:
        done = run(["systemctl", "--user", "show", "-p", "ActiveEnterTimestamp", "--value", UNIT], env)
        shown = done.stdout.strip()
        m = START.match(shown) if done.returncode == 0 else None
        if m:
            start = calendar.timegm(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    except (OSError, subprocess.SubprocessError) as error:
        shown = str(error)
    if start is None:
        print(f"Couldn't read when the gateway started ({shown[-200:]}), so it was not restarted. "
              "If gateway.toml changed, restart it yourself with no runs live: "
              f"systemctl --user restart {UNIT}", file=sys.stderr)
        return 0
    try:
        changed = int(config.stat().st_mtime)
    except OSError as error:
        raise Stop(f"can't read {config}: {error}")
    if changed <= start:
        return 0
    try:
        done = run(["openshell", "sandbox", "list", "--names", "--page-size", "1000"])
        failed = done.returncode != 0
    except (OSError, subprocess.SubprocessError):
        failed = True
    if failed:
        print("Couldn't list the sandboxes, so the gateway was not restarted and still runs on "
              "the old gateway.toml. Rerun 'just openshell-setup'.", file=sys.stderr)
        return 0
    names = done.stdout.split()
    if names:
        print(f"gateway.toml is newer than the running gateway, but the gateway was not restarted: "
              f"{len(names)} sandbox(es) are live ({', '.join(names[:10])}). A restart stops every "
              "sandbox, other projects' runs included. Rerun 'just openshell-setup' once those "
              "runs end.", file=sys.stderr)
        return 0
    try:
        done = run(["systemctl", "--user", "restart", UNIT])
    except (OSError, subprocess.SubprocessError) as error:
        raise Stop(f"systemctl restart failed: {error}")
    if done.returncode != 0:
        raise Stop(f"systemctl restart failed: {last(done.stderr)}")
    try:
        got = table_of(tomllib.loads(config.read_bytes().decode("utf-8")))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        got = {}
    if all(good(got.get(k)) for k in DEFAULTS):
        print(f"Restarted the gateway: new sandboxes get {got['vcpus']} vCPUs, {got['mem_mib']} MiB "
              f"of RAM and a {got['overlay_disk_mib']} MiB disk.")
    else:
        print("Restarted the gateway.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["size", "restart"])
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    if os.environ.get("SSSF_ADW_ID"):
        print("Refusing: changing the OpenShell gateway is the engineer's to run, "
              "outside an SSSF run (SSSF_ADW_ID is set).", file=sys.stderr)
        return 1
    config = Path(args.config)
    try:
        return size(config) if args.command == "size" else restart(config)
    except Stop as error:
        print(f"openshell_gateway: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
