"""pi's extension cache: jiti reuses a compiled copy from <TMPDIR>/jiti after checking only its last
line. With JITI_FS_CACHE=false in the environment (agent_pi.NO_EXTENSION_CACHE) it reads none.

Every run here has TMPDIR under tmp_path, so the real /tmp/jiti and /tmp/node-compile-cache are
never read or written. Skipped without node or pi: the planted-cache proof is then left for the
engineer's proof."""
from __future__ import annotations

import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))
from adw_modules import agent_pi  # noqa: E402

PROBE = Path(__file__).resolve().parent / "fixtures" / "jiti" / "probe.mjs"


def _jiti_dir() -> Path | None:
    pi = shutil.which("pi")
    if pi is None:
        return None
    for folder in Path(pi).resolve().parents:
        if (folder / "package.json").is_file() and (folder / "node_modules" / "jiti" / "package.json").is_file():
            return folder / "node_modules" / "jiti"
    return None


JITI = _jiti_dir()
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or JITI is None,
    reason="node, pi or pi's jiti is missing; the planted-cache proof is left for the engineer's proof")


def _env(tmp: Path, **extra: str) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if k == "PATH"}
    return {**base, "TMPDIR": str(tmp), **extra}


def _extension(tmp_path: Path) -> Path:
    path = tmp_path / "ext.ts"
    path.write_text('export default function (): string {\n  return "source";\n}\n')
    return path


def _load(tmp_path: Path, tmp: Path, ext: Path, **extra: str) -> str:
    done = subprocess.run(["node", str(PROBE), str(JITI), str(ext)], env=_env(tmp, **extra),
                          capture_output=True, text=True, timeout=60, cwd=str(tmp_path))
    assert done.returncode == 0, done.stderr
    return done.stdout


def _plant(tmp: Path) -> Path:
    files = [p for p in (tmp / "jiti").iterdir() if p.is_file()]
    assert len(files) == 1
    text = files[0].read_text()
    assert re.search(r"/\* v9-[0-9a-f]{16} \*/\n$", text)
    assert "source" in text
    files[0].write_text(text.replace('"source"', '"plant"'))
    return files[0]


def test_a_planted_cache_entry_runs_without_the_setting(tmp_path):
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    ext = _extension(tmp_path)
    assert _load(tmp_path, tmp, ext) == "source"
    _plant(tmp)
    assert _load(tmp_path, tmp, ext) == "plant"      # the control: the baseline's behaviour


def test_with_the_setting_the_source_runs(tmp_path):
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    ext = _extension(tmp_path)
    _load(tmp_path, tmp, ext)
    planted = _plant(tmp)
    before = planted.read_bytes()
    assert _load(tmp_path, tmp, ext, **agent_pi.NO_EXTENSION_CACHE) == "source"
    assert planted.read_bytes() == before


def test_with_the_setting_no_cache_is_made(tmp_path):
    tmp = tmp_path / "tmp"
    tmp.mkdir()
    assert _load(tmp_path, tmp, _extension(tmp_path), **agent_pi.NO_EXTENSION_CACHE) == "source"
    assert not (tmp / "jiti").exists()


@pytest.mark.skipif(shutil.which("pi") is None, reason="pi is not installed")
def test_the_cost_on_a_real_pi(tmp_path):
    home, agent, tmp = tmp_path / "home", tmp_path / "agent", tmp_path / "tmp"
    for d in (home, agent, tmp):
        d.mkdir()
    env = {**_env(tmp), "HOME": str(home), "PI_CODING_AGENT_DIR": str(agent), "PI_OFFLINE": "1"}
    pi = shutil.which("pi")
    argv = [pi, "--list-models", *agent_pi.HOST_FLAGS, "--no-approve", "--offline",
            "-e", str(agent_pi.REFUSE_EXTENSION)]

    def timed(extra: dict[str, str]) -> float:
        start = time.monotonic()
        done = subprocess.run(argv, env={**env, **extra}, cwd="/", capture_output=True, timeout=120)
        assert done.returncode == 0, done.stderr
        return time.monotonic() - start

    timed({})                                          # warm
    warm = statistics.median(timed({}) for _ in range(3))
    before = sorted((tmp / "jiti").iterdir())
    off = statistics.median(timed(agent_pi.NO_EXTENSION_CACHE) for _ in range(3))
    assert sorted((tmp / "jiti").iterdir()) == before
    print(f"warm cache median {warm:.2f}s, JITI_FS_CACHE=false median {off:.2f}s")
    assert off < warm + 2

    # The host's pi loads no extension through jiti.
    fresh, again = tmp_path / "fresh", {}
    fresh.mkdir()
    again = {**env, "TMPDIR": str(fresh)}
    done = subprocess.run([pi, "--list-models", *agent_pi.HOST_FLAGS, "--offline"], env=again,
                          cwd="/", capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert not (fresh / "jiti").exists()
