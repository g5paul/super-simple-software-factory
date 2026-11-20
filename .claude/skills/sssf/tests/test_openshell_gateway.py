"""scripts/openshell_gateway.py sizes the gateway's VMs and restarts it only when owed.

Every command is a stand-in that logs its call, and HOME is a temp folder, so nothing
here lists, restarts or writes anything real.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import ast
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "scripts" / "openshell_gateway.py"

JUST = shutil.which("just")
needs_just = pytest.mark.skipif(not JUST, reason="just isn't installed")

FRESH = ('[openshell]\nversion = 2\n\n[openshell.gateway]\nbind_address = "127.0.0.1:17670"\n'
         'compute_driver = "vm"\n')
SIZED = FRESH + "\n[openshell.drivers.vm]\nvcpus = 4\nmem_mib = 4096\noverlay_disk_mib = 16384\n"

FAKE = """#!/bin/sh
name=$(basename "$0")
echo "$name $*" >> "$STANDIN_LOG"
case "$name $1 $2" in
  "openshell sandbox list") printf '%s\\n' "$FAKE_SANDBOXES"; exit "${FAKE_LIST_CODE:-0}";;
  "openshell-gateway config preflight") exit "${FAKE_PREFLIGHT_CODE:-0}";;
esac
if [ "$name" = systemctl ]; then
  case "$*" in
    *" show "*) echo "TZ=$TZ" >> "$STANDIN_LOG"; printf '%s\\n' "$FAKE_ACTIVE_ENTER"
                exit "${FAKE_SHOW_CODE:-0}";;
  esac
fi
exit 0
"""


def standins(tmp_path: Path) -> Path:
    folder = tmp_path / "bin"
    folder.mkdir(exist_ok=True)
    for name in ("openshell", "openshell-gateway", "systemctl"):
        path = folder / name
        path.write_text(FAKE)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return folder


def environment(tmp_path: Path, **extra: str) -> dict[str, str]:
    """The suite runs inside an agent, so SSSF_ADW_ID and SSSF_AGENT come out first."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = dict(os.environ)
    for name in ("SSSF_ADW_ID", "SSSF_AGENT", "SSSF_OPENSHELL_VCPUS", "SSSF_OPENSHELL_MEM_MIB",
                 "SSSF_OPENSHELL_OVERLAY_DISK_MIB"):
        env.pop(name, None)
    env.update(HOME=str(home), PATH=f"{standins(tmp_path)}{os.pathsep}{os.environ['PATH']}",
               STANDIN_LOG=str(tmp_path / "standin.log"), FAKE_SANDBOXES="", FAKE_ACTIVE_ENTER="")
    env.update(extra)
    return env


def calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "standin.log"
    return log.read_text().splitlines() if log.exists() else []


def conf_at(tmp_path: Path, text: str | bytes = FRESH) -> Path:
    conf = tmp_path / "home" / ".config" / "openshell" / "gateway.toml"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_bytes(text.encode() if isinstance(text, str) else text)
    return conf


def script(tmp_path: Path, command: str, conf: Path, **extra: str):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), command, "--config", str(conf)],
                          env=environment(tmp_path, **extra), capture_output=True, text=True)


def stamp(seconds: float) -> str:
    return time.strftime("%a %Y-%m-%d %H:%M:%S UTC", time.gmtime(seconds))


def started_before(conf: Path) -> str:
    return stamp(conf.stat().st_mtime - 3600)


def started_after(conf: Path) -> str:
    return stamp(conf.stat().st_mtime + 3600)


def restarts(tmp_path: Path) -> list[str]:
    return [c for c in calls(tmp_path) if c.startswith("systemctl --user restart")]


# --- size ----------------------------------------------------------------------

def test_size_on_a_fresh_file_gives_exactly_the_shipped_bytes(tmp_path):
    conf = conf_at(tmp_path)
    done = script(tmp_path, "size", conf)
    assert done.returncode == 0, done.stderr
    assert conf.read_bytes() == SIZED.encode()
    assert [p.name for p in conf.parent.iterdir()] == ["gateway.toml"]
    assert [p for p in (tmp_path / "home").rglob("*") if p.is_file()] == [conf]


EXISTING = ("# mine\n[openshell]\nversion = 2\n\n[openshell.gateway]\nbind_address = \"127.0.0.1:17670\"\r\n"
            "compute_driver = \"vm\"\nextra = 1\n\n[openshell.drivers.docker]\nimage = \"x\"\n\n"
            "[openshell.drivers.vm]  # the vm\nmem_mib = 2048  # mine\nother = \"keep\"\n\n[later]\nk = 1\n")


def test_size_in_an_existing_file_changes_only_the_sizes(tmp_path):
    conf = conf_at(tmp_path, EXISTING)
    done = script(tmp_path, "size", conf, SSSF_OPENSHELL_MEM_MIB="8192")
    assert done.returncode == 0, done.stderr
    new = conf.read_bytes().decode()
    assert "mem_mib = 8192  # mine\n" in new
    assert "[openshell.drivers.vm]  # the vm\nvcpus = 4\noverlay_disk_mib = 16384\nmem_mib = 8192" in new
    back = new.replace("vcpus = 4\n", "").replace("overlay_disk_mib = 16384\n", "")
    assert back.replace("8192", "2048") == EXISTING
    assert "vcpus = 4\n" in new and "overlay_disk_mib = 16384\n" in new
    assert "vcpus" in done.stdout and "mem_mib 2048 -> 8192" in done.stdout and "overlay_disk_mib" in done.stdout


def test_a_second_size_run_changes_nothing_and_restarts_nothing(tmp_path):
    conf = conf_at(tmp_path, EXISTING)
    env = {"SSSF_OPENSHELL_MEM_MIB": "8192"}
    assert script(tmp_path, "size", conf, **env).returncode == 0
    before, mtime = conf.read_bytes(), conf.stat().st_mtime_ns
    done = script(tmp_path, "size", conf, **env)
    assert done.returncode == 0 and "unchanged" in done.stdout
    assert conf.read_bytes() == before and conf.stat().st_mtime_ns == mtime
    done = script(tmp_path, "restart", conf, FAKE_ACTIVE_ENTER=started_after(conf))
    assert done.returncode == 0
    assert not [c for c in calls(tmp_path) if c.startswith("openshell ")] and not restarts(tmp_path)


def test_a_value_in_the_file_is_kept(tmp_path):
    conf = conf_at(tmp_path, FRESH + "\n[openshell.drivers.vm]\nvcpus = 6\n")
    assert script(tmp_path, "size", conf).returncode == 0
    vm = tomllib.loads(conf.read_text())["openshell"]["drivers"]["vm"]
    assert vm == {"vcpus": 6, "mem_mib": 4096, "overlay_disk_mib": 16384}


@pytest.mark.parametrize("bad", ['"many"', "0", "-3", "true"])
def test_a_bad_value_in_the_file_becomes_the_default(tmp_path, bad):
    conf = conf_at(tmp_path, FRESH + f"\n[openshell.drivers.vm]\nvcpus = {bad}\n")
    done = script(tmp_path, "size", conf)
    assert done.returncode == 0, done.stderr
    assert tomllib.loads(conf.read_text())["openshell"]["drivers"]["vm"]["vcpus"] == 4
    assert re.findall(r"^vcpus", conf.read_text(), re.M) == ["vcpus"]
    assert "vcpus" in done.stdout and "using 4" in done.stdout


@pytest.mark.parametrize("key", ["vcpus", "mem_mib", "overlay_disk_mib"])
def test_each_override_sets_its_size(tmp_path, key):
    conf = conf_at(tmp_path)
    name = {"vcpus": "SSSF_OPENSHELL_VCPUS", "mem_mib": "SSSF_OPENSHELL_MEM_MIB",
            "overlay_disk_mib": "SSSF_OPENSHELL_OVERLAY_DISK_MIB"}[key]
    assert script(tmp_path, "size", conf, **{name: "7"}).returncode == 0
    assert f"\n{key} = 7\n" in conf.read_text()


@pytest.mark.parametrize("name", ["SSSF_OPENSHELL_VCPUS", "SSSF_OPENSHELL_MEM_MIB",
                                  "SSSF_OPENSHELL_OVERLAY_DISK_MIB"])
@pytest.mark.parametrize("value", ["0", "-1", "1.5", "abc", "4 ", "٤"])
def test_a_bad_override_is_refused_with_its_name(tmp_path, name, value):
    conf = conf_at(tmp_path)
    before, mtime = conf.read_bytes(), conf.stat().st_mtime_ns
    done = script(tmp_path, "size", conf, **{name: value})
    assert done.returncode != 0 and name in done.stderr
    assert conf.read_bytes() == before and conf.stat().st_mtime_ns == mtime


def test_an_empty_override_counts_as_unset(tmp_path):
    conf = conf_at(tmp_path)
    assert script(tmp_path, "size", conf, SSSF_OPENSHELL_VCPUS="").returncode == 0
    assert conf.read_bytes() == SIZED.encode()


def test_a_layout_it_cant_edit_is_left_alone(tmp_path):
    text = "[openshell]\nversion = 2\ndrivers = { vm = { vcpus = 2 } }\n"
    conf = conf_at(tmp_path, text)
    done = script(tmp_path, "size", conf)
    assert done.returncode != 0 and "by hand" in done.stderr
    assert conf.read_text() == text


def test_a_missing_or_bad_file_is_refused(tmp_path):
    done = script(tmp_path, "size", tmp_path / "home" / "none.toml")
    assert done.returncode != 0 and "none.toml" in done.stderr
    conf = conf_at(tmp_path, b"\xff\xfe not utf-8")
    assert script(tmp_path, "size", conf).returncode != 0
    conf = conf_at(tmp_path, "[broken\n")
    assert script(tmp_path, "size", conf).returncode != 0 and conf.read_text() == "[broken\n"


def test_a_preflight_refusal_leaves_the_file_and_no_temp_file(tmp_path):
    conf = conf_at(tmp_path)
    done = script(tmp_path, "size", conf, FAKE_PREFLIGHT_CODE="1")
    assert done.returncode != 0
    assert conf.read_bytes() == FRESH.encode()
    assert [p.name for p in conf.parent.iterdir()] == ["gateway.toml"]
    assert any(c.startswith("openshell-gateway config preflight --path") for c in calls(tmp_path))


def test_a_symlinked_file_keeps_its_link(tmp_path):
    real = tmp_path / "dotfiles" / "gateway.toml"
    real.parent.mkdir()
    real.write_text(FRESH)
    link = tmp_path / "home" / ".config" / "openshell" / "gateway.toml"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    assert script(tmp_path, "size", link).returncode == 0
    assert link.is_symlink() and real.read_bytes() == SIZED.encode()


# --- restart -------------------------------------------------------------------

def test_no_restart_under_a_live_run_and_one_once_it_ends(tmp_path):
    conf = conf_at(tmp_path)
    assert script(tmp_path, "size", conf).returncode == 0
    old = started_before(conf)
    done = script(tmp_path, "restart", conf, FAKE_ACTIVE_ENTER=old, FAKE_SANDBOXES="sssf-abc12345-0a1b")
    assert done.returncode == 0 and not restarts(tmp_path)
    assert "sssf-abc12345-0a1b" in done.stderr and "just openshell-setup" in done.stderr
    done = script(tmp_path, "restart", conf, FAKE_ACTIVE_ENTER=old, FAKE_SANDBOXES="")
    assert done.returncode == 0, done.stderr
    assert restarts(tmp_path) == ["systemctl --user restart openshell-gateway"]
    assert "4 vCPUs" in done.stdout and "4096 MiB" in done.stdout and "16384 MiB" in done.stdout


def test_a_failed_list_restarts_nothing(tmp_path):
    conf = conf_at(tmp_path)
    done = script(tmp_path, "restart", conf, FAKE_ACTIVE_ENTER=started_before(conf), FAKE_LIST_CODE="1")
    assert done.returncode == 0 and not restarts(tmp_path)
    assert "not restarted" in done.stderr


def test_a_gateway_started_after_the_file_is_left_alone(tmp_path):
    conf = conf_at(tmp_path)
    done = script(tmp_path, "restart", conf, FAKE_ACTIVE_ENTER=started_after(conf),
                  FAKE_SANDBOXES="sssf-abc12345-0a1b")
    assert done.returncode == 0 and done.stderr == ""
    assert not [c for c in calls(tmp_path) if c.startswith("openshell ")] and not restarts(tmp_path)


def test_the_same_second_is_not_owed(tmp_path):
    conf = conf_at(tmp_path)
    done = script(tmp_path, "restart", conf, FAKE_ACTIVE_ENTER=stamp(int(conf.stat().st_mtime)))
    assert done.returncode == 0
    assert not [c for c in calls(tmp_path) if c.startswith("openshell ")] and not restarts(tmp_path)


@pytest.mark.parametrize("shown, code", [("", "0"), ("n/a", "0"), ("Fri 2026-11-20 08:13:05 PST", "0"),
                                         ("Fri 2026-11-20 16:13:05 UTC", "1")])
def test_a_start_time_it_cant_read_restarts_nothing(tmp_path, shown, code):
    conf = conf_at(tmp_path)
    done = script(tmp_path, "restart", conf, FAKE_ACTIVE_ENTER=shown, FAKE_SHOW_CODE=code)
    assert done.returncode == 0 and "not restarted" in done.stderr
    assert not [c for c in calls(tmp_path) if c.startswith("openshell ")] and not restarts(tmp_path)


def test_the_start_time_is_asked_for_in_utc(tmp_path):
    conf = conf_at(tmp_path)
    script(tmp_path, "restart", conf, FAKE_ACTIVE_ENTER=started_after(conf))
    log = calls(tmp_path)
    show = next(c for c in log if c.startswith("systemctl --user show"))
    assert "ActiveEnterTimestamp" in show and "openshell-gateway" in show
    assert "TZ=UTC" in log


@pytest.mark.parametrize("command", ["size", "restart"])
def test_both_refuse_inside_a_run(tmp_path, command):
    conf = conf_at(tmp_path)
    before = conf.read_bytes()
    done = script(tmp_path, command, conf, SSSF_ADW_ID="abc12345", FAKE_ACTIVE_ENTER=started_before(conf))
    assert done.returncode != 0
    assert len(done.stderr.splitlines()) == 1
    assert "engineer's to run" in done.stderr and "outside an SSSF run" in done.stderr
    assert calls(tmp_path) == [] and conf.read_bytes() == before


def test_the_header_matches_the_imports():
    text = SCRIPT.read_text()
    assert "# dependencies = []" in text
    found = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            found.add(node.module.split(".")[0])
    assert found and found <= set(sys.stdlib_module_names)


# --- through the recipe ----------------------------------------------------------

UV = """#!/bin/sh
echo "uv $*" >> "$STANDIN_LOG"
if [ "$1 $2" = "run .claude/skills/sssf/scripts/openshell_gateway.py" ]; then
  shift 2; exec "$REAL_PYTHON" -I "$SCRIPT_PATH" "$@"
fi
exit 0
"""


def recipe_run(tmp_path: Path, **extra: str):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    kvm = tmp_path / "kvm"
    kvm.write_text("")
    text = (SKILL / "templates" / "justfile").read_text().replace("/dev/kvm", str(kvm))
    (repo / "justfile").write_text(text)
    bin_dir = standins(tmp_path)
    for name in ("podman", "uv"):
        path = bin_dir / name
        path.write_text(UV if name == "uv" else FAKE)
        path.chmod(0o755)
    env = environment(tmp_path, REAL_PYTHON=sys.executable, SCRIPT_PATH=str(SCRIPT), **extra)
    return subprocess.run([JUST, "openshell-setup"], cwd=repo, capture_output=True, text=True, env=env)


@needs_just
# host_only, the error names the sandbox: runs a just recipe that writes where agentsh denies.
@pytest.mark.host_only
def test_recipe_sizes_a_fresh_gateway_without_restarting(tmp_path):
    future = stamp(time.time() + 3600)
    done = recipe_run(tmp_path, FAKE_ACTIVE_ENTER=future)
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "home/.config/openshell/gateway.toml").read_bytes() == SIZED.encode()
    log = calls(tmp_path)
    assert "systemctl --user enable --now openshell-gateway" in log
    assert not restarts(tmp_path)


@needs_just
# host_only, the error names the sandbox: runs a just recipe that writes where agentsh denies.
@pytest.mark.host_only
def test_recipe_restarts_once_the_runs_end(tmp_path):
    conf = conf_at(tmp_path)
    old = stamp(946684800)
    done = recipe_run(tmp_path, FAKE_ACTIVE_ENTER=old, FAKE_SANDBOXES="sssf-abc12345-0a1b")
    assert done.returncode == 0, done.stderr
    assert conf.read_bytes() == SIZED.encode() and not restarts(tmp_path)
    assert "just openshell-setup" in done.stderr
    done = recipe_run(tmp_path, FAKE_ACTIVE_ENTER=old, FAKE_SANDBOXES="")
    assert done.returncode == 0, done.stderr
    assert len(restarts(tmp_path)) == 1 and conf.read_bytes() == SIZED.encode()
    done = recipe_run(tmp_path, FAKE_ACTIVE_ENTER=stamp(time.time() + 3600))
    assert done.returncode == 0, done.stderr
    assert len(restarts(tmp_path)) == 1 and conf.read_bytes() == SIZED.encode()
