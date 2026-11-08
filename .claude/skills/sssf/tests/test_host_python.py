"""sandbox_remote._host_python: the host interpreter's answer is checked before
the OpenShell image, or a VM's UV_PYTHON, uses it. Stand-ins for uv, the
interpreter, openshell and podman run first on PATH.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "scripts" / "openshell_image.py"
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import sandbox, sandbox_openshell as osh, sandbox_remote  # noqa: E402
from adw_modules.sandbox import SandboxFailed  # noqa: E402

BAD = [b"3.13\nhello from sitecustomize", b"hello\n3.13", b"3.13.1", b"3",
       "٣.١٣".encode(), b"3.13; curl x|sh", b"\xff\xfe", b""]


def script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def host(tmp_path, monkeypatch, answer: bytes = b"3.13\n", uv: str | None = None):
    """A repo under a temp HOME with the shipped config, and stand-ins first on PATH."""
    home = tmp_path / "home"
    repo = home / "repo"
    repo.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    subprocess.run(["git", "init", "-q", "-b", "sssf/abc", str(repo)], check=True)
    for key, value in (("user.name", "t"), ("user.email", "t@t")):
        subprocess.run(["git", "config", key, value], cwd=repo, check=True)
    config = repo / osh.CONFIG
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text((SKILL / "templates" / "sssf.config.yaml").read_text())
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "c"], cwd=repo, check=True, capture_output=True)
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (tmp_path / "answer").write_bytes(answer)
    interp = script(bin_ / "interp", f'echo "$*" >> {tmp_path}/interp.log\ncat {tmp_path}/answer\n')
    script(bin_ / "uv", uv if uv is not None else
           f'echo "$PWD $*" >> {tmp_path}/uv.log\necho {interp}\n')
    for name in ("openshell", "podman"):
        script(bin_ / name, f'echo "{name} $*" >> {tmp_path}/calls.log\n')
    monkeypatch.setenv("PATH", f"{bin_}:{os.environ['PATH']}")
    return types.SimpleNamespace(repo=repo, interp=interp, bin=bin_, tmp=tmp_path,
                                 calls=lambda: (tmp_path / "calls.log").read_text()
                                 if (tmp_path / "calls.log").exists() else "")


def problems_of(tree: Path) -> list[str]:
    problems: list[str] = []
    assert sandbox_remote._host_python(tree, problems) == ""
    return problems


@pytest.mark.parametrize("answer", BAD)
def test_a_bad_answer_gives_nothing_and_one_problem(tmp_path, monkeypatch, answer):
    h = host(tmp_path, monkeypatch, answer)
    assert sandbox_remote._host_python(h.repo) == ""
    problems = problems_of(h.repo)
    assert len(problems) == 1 and "\n" not in problems[0]
    assert str(h.interp) in problems[0]


def test_a_long_answer_is_cut(tmp_path, monkeypatch):
    h = host(tmp_path, monkeypatch, b"x" * 100_000)
    (problem,) = problems_of(h.repo)
    assert len(problem) < 1000


def test_an_escape_code_stays_inert(tmp_path, monkeypatch):
    h = host(tmp_path, monkeypatch, b"\x1b[2J3.13")
    (problem,) = problems_of(h.repo)
    assert "\x1b" not in problem


def test_a_good_answer_passes(tmp_path, monkeypatch):
    h = host(tmp_path, monkeypatch, b"3.13\n")
    problems: list[str] = []
    assert sandbox_remote._host_python(h.repo, problems) == "3.13"
    assert problems == []


NO_PYTHON = {
    "uv exits 1": "exit 1\n",
    "uv prints nothing": "exit 0\n",
    "missing interpreter": "echo /nonexistent/python\n",
}


@pytest.mark.parametrize("uv", NO_PYTHON.values(), ids=NO_PYTHON)
def test_no_python_gives_no_problem(tmp_path, monkeypatch, uv):
    h = host(tmp_path, monkeypatch, uv=uv)
    assert problems_of(h.repo) == []
    assert osh.image_python(h.repo) == ""


def test_no_uv_on_path_gives_no_problem(tmp_path, monkeypatch):
    h = host(tmp_path, monkeypatch)
    (h.bin / "uv").unlink()
    (h.bin / "git").symlink_to(shutil.which("git"))     # repo_root needs git, not uv
    monkeypatch.setenv("PATH", str(h.bin))
    assert problems_of(h.repo) == []
    assert osh.image_python(h.repo) == ""


def test_an_interpreter_that_exits_1_gives_no_problem(tmp_path, monkeypatch):
    h = host(tmp_path, monkeypatch)
    script(h.interp, "exit 1\n")
    assert problems_of(h.repo) == []
    assert osh.image_python(h.repo) == ""


@pytest.mark.parametrize("printed", [r"\377", r"a\0b"])
def test_bad_output_from_uv_itself(tmp_path, monkeypatch, printed):
    h = host(tmp_path, monkeypatch, uv=f"printf '{printed}\\n'\n")
    assert len(problems_of(h.repo)) == 1


def test_the_interpreter_runs_isolated(tmp_path, monkeypatch):
    h = host(tmp_path, monkeypatch)
    assert sandbox_remote._host_python(h.repo) == "3.13"
    assert (h.tmp / "interp.log").read_text().startswith("-I -S -c ")


def _venv(tmp_path):
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    site = next(venv.glob("lib/python*/site-packages"))
    return venv / "bin" / "python", site


@pytest.mark.parametrize("route", ["pythonpath", "sitecustomize", "pth"])
def test_startup_code_no_longer_reaches_the_answer(tmp_path, monkeypatch, route):
    h = host(tmp_path, monkeypatch)
    if route == "pythonpath":
        folder = tmp_path / "startup"
        folder.mkdir()
        (folder / "sitecustomize.py").write_text("print('hello')\n")
        monkeypatch.setenv("PYTHONPATH", str(folder))
        python = Path(sys.executable)
    else:
        python, site = _venv(tmp_path)
        if route == "sitecustomize":
            (site / "sitecustomize.py").write_text("print('hello')\n")
        else:
            (site / "hello.pth").write_text("import sys; print('hello')\n")
    control = subprocess.run([str(python), "-c", "pass"], capture_output=True, text=True)
    assert "hello" in control.stdout
    script(h.bin / "uv", f"echo {python}\n")
    problems: list[str] = []
    want = "%d.%d" % sys.version_info[:2]
    assert sandbox_remote._host_python(h.repo, problems) == want
    assert problems == []


def test_uv_python_find_is_called_as_before(tmp_path, monkeypatch):
    h = host(tmp_path, monkeypatch)
    real, calls = subprocess.run, []

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)
    monkeypatch.setattr(sandbox_remote.subprocess, "run", spy)
    sandbox_remote._host_python(h.repo)
    assert len(calls) == 2
    assert calls[0][0] == (["uv", "python", "find"],)
    assert calls[0][1] == {"cwd": h.repo, "capture_output": True, "text": True, "timeout": 60}


def bad_one_line(text: str, h) -> None:
    assert "\n" not in text
    assert str(h.interp) in text and repr("hello") in text


def test_require_sdk_stops_in_one_line(tmp_path, monkeypatch):
    h = host(tmp_path, monkeypatch, b"hello\n")
    monkeypatch.chdir(h.repo)
    with pytest.raises(SystemExit) as stop:
        osh.require_sdk()
    bad_one_line(str(stop.value), h)
    assert "podman" not in h.calls()


def open_box(h, monkeypatch, cwd=None):
    token = h.tmp / "token.env"
    token.write_text("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-test\n")
    monkeypatch.setattr(sandbox_remote, "TOKEN_FILE", token)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    session = h.repo / "adws" / "adw_data" / "sessions" / "abc"
    handoff = session / "context_handoff"
    handoff.mkdir(parents=True)
    run = types.SimpleNamespace(adw_id="abc", context_handoff_dir=handoff, session_dir=session,
                                tracer=types.SimpleNamespace(event=lambda e: None),
                                console=types.SimpleNamespace(note=lambda t: None))
    monkeypatch.chdir(cwd or h.repo)
    return osh.OpenShell(run, "sssf/abc", "my repo")


def test_create_and_open_refuse_a_bad_answer_in_the_main_checkout(tmp_path, monkeypatch):
    h = host(tmp_path, monkeypatch, b"hello\n")
    box = open_box(h, monkeypatch)
    with pytest.raises(osh.OpenShellError) as error:
        box._create()
    bad_one_line(str(error.value), h)
    with pytest.raises(SandboxFailed) as failed:
        box.open()
    bad_one_line(str(failed.value), h)
    assert "sandbox create" not in h.calls()


def test_open_refuses_a_bad_answer_in_the_worktree_alone(tmp_path, monkeypatch):
    h = host(tmp_path, monkeypatch)
    worktree = h.repo / "adws" / "adw_data" / "worktrees" / "abc"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "other", str(worktree)],
                   cwd=h.repo, check=True, capture_output=True)
    good = h.interp
    bad = script(h.bin / "interp-bad", "echo hello\n")
    script(h.bin / "uv", f'case "$PWD" in {worktree}*) echo {bad};; *) echo {good};; esac\n')
    (h.tmp / "answer").write_bytes(b"3.13\n")
    assert osh.image_python(h.repo) == "3.13"
    box = open_box(h, monkeypatch, cwd=worktree)
    with pytest.raises(SandboxFailed) as failed:
        box.open()
    assert str(bad) in str(failed.value) and "\n" not in str(failed.value)
    assert "sandbox create" not in h.calls()


@pytest.mark.parametrize("show", [True, False])
def test_the_script_exits_2_in_one_line(tmp_path, monkeypatch, show):
    h = host(tmp_path, monkeypatch, b"hello\n")
    env = {k: v for k, v in os.environ.items() if k != "SSSF_ADW_ID"}
    args = [sys.executable, "-I", str(SCRIPT), "--repo", str(h.repo)] + (["--print"] if show else [])
    done = subprocess.run(args, env=env, capture_output=True, text=True)
    assert done.returncode == 2 and done.stdout == ""
    (line,) = done.stderr.splitlines()
    assert line.startswith("openshell_image:")
    bad_one_line(line, h)
    assert "Traceback" not in done.stderr and "podman" not in h.calls()


def test_a_base_remote_runs_on_with_no_python(tmp_path, monkeypatch):
    h = host(tmp_path, monkeypatch, b"hello\n")
    token = h.tmp / "token.env"
    token.write_text("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat-test\n")
    monkeypatch.setattr(sandbox_remote, "TOKEN_FILE", token)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.chdir(h.repo)

    class Stop(Exception):
        pass

    class Plain(sandbox_remote.Remote):
        keys = ()

        def _create(self):
            raise Stop

    run = types.SimpleNamespace(adw_id="abc", context_handoff_dir=h.repo, session_dir=h.repo,
                                tracer=types.SimpleNamespace(event=lambda e: None),
                                console=types.SimpleNamespace(note=lambda t: None))
    box = Plain(run, "sssf/abc", "my repo")
    with pytest.raises(Stop):
        box.open()
    assert box.python == "" and "hello" in box.python_problem
