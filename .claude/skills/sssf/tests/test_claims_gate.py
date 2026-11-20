"""gates.diff_matches_claims: a claimed path passes when it exists, or when it is a
file in the commit the call started from that is gone from the working tree (a
deleted file, a rename's old path). The start commit is handed over by
agents.execute as `start`; the gate reads its file names with one isolated
`git ls-tree` of that full id, never of HEAD.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests/test_claims_gate.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from adw_modules import agents, gates, sandbox, utils
from adw_modules.data_types import AgentResult, BuildOutput

from test_failed_call_rollback import git as rgit  # noqa: E402,F401
from test_failed_call_rollback import world, write  # noqa: E402,F401

MISSING = "claimed changed file does not exist"
DELETED = "deleted in the working tree"


def command(start: str) -> list[str]:
    return ["env", "GIT_ALLOW_PROTOCOL=", "GIT_NO_LAZY_FETCH=1", "GIT_GRAFT_FILE=/dev/null", "LC_ALL=C",
            "git", "--no-replace-objects", "-c", "core.fsmonitor=", "-c", "core.hooksPath=/dev/null",
            "-c", "core.commitGraph=false", "-c", "advice.graftFileDeprecated=false",
            "ls-tree", "-r", "-z", "--name-only", "--full-tree", start]


def git(cwd: Path, *args: str, text: bool = True) -> str:
    done = subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
                          cwd=cwd, check=True, capture_output=True, text=text)
    return done.stdout.strip() if text else done.stdout


class Recording(sandbox.Local):
    def __init__(self):
        self.calls: list[tuple] = []

    def run(self, command, **kw):
        self.calls.append(("run", list(command), kw))
        return super().run(command, **kw)

    def exists(self, path):
        self.calls.append(("exists", path))
        return super().exists(path)

    def size(self, path):
        self.calls.append(("size", path))
        return super().size(path)

    def runs(self):
        return [c for c in self.calls if c[0] == "run"]


@pytest.fixture
def box(monkeypatch):
    b = Recording()
    monkeypatch.setattr(sandbox, "_current", b)
    return b


@pytest.fixture
def repo(tmp_path, monkeypatch, box):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.txt").write_text("a\n")
    (root / "gone.txt").write_text("gone\n")
    (root / "keep.txt").write_text("keep\n")
    (root / "dir").mkdir()
    (root / "dir" / "f.txt").write_text("f\n")
    (tmp_path / "outside.txt").write_text("x\n")
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "first")
    monkeypatch.chdir(root)
    box.calls.clear()
    return root


@pytest.fixture
def start(repo):
    return git(repo, "rev-parse", "HEAD")


def gate(start, *claims: str):
    return gates.diff_matches_claims(BuildOutput(status="success", changed_files=list(claims)), None,
                                     start=start)


def notes(report) -> dict[str, str]:
    return {c.item: c.note for c in report.checks}


@pytest.mark.parametrize("how", ["git", "plain"])
def test_a_rename_passes(repo, start, how):
    if how == "git":
        git(repo, "mv", "gone.txt", "new.txt")
    else:
        (repo / "gone.txt").rename(repo / "new.txt")
    report = gate(start, "gone.txt", "new.txt")
    assert report.passed, report.violations
    assert notes(report)["gone.txt"] == DELETED
    assert notes(report)["new.txt"].startswith("exists, ")


@pytest.mark.parametrize("how", ["git", "plain"])
def test_a_delete_passes(repo, start, how):
    if how == "git":
        git(repo, "rm", "-q", "gone.txt")
    else:
        (repo / "gone.txt").unlink()
    report = gate(start, "gone.txt")
    assert report.passed, report.violations
    assert notes(report) == {"gone.txt": DELETED}


def _never_existed(repo):
    return "nothing.txt"


def _untracked_removed(repo):
    (repo / "tmp.txt").write_text("t\n")
    (repo / "tmp.txt").unlink()
    return "tmp.txt"


def _outside(repo):
    return "../nothing_outside.txt"


def _absolute_outside(repo):
    return str(repo.parent / "nothing_outside.txt")


def _absolute_real(repo):
    (repo / "gone.txt").unlink()
    return str(repo / "gone.txt")


def _dot_slash(repo):
    (repo / "gone.txt").unlink()
    return "./gone.txt"


def _double_slash(repo):
    (repo / "src" / "a.txt").unlink()
    return "src//a.txt"


def _dotdot(repo):
    (repo / "gone.txt").unlink()
    return "src/../gone.txt"


def _folder(repo):
    (repo / "dir" / "f.txt").unlink()
    (repo / "dir").rmdir()
    return "dir"


MAKES = [_never_existed, _untracked_removed, _outside, _absolute_outside, _absolute_real,
         _dot_slash, _double_slash, _dotdot, _folder]


@pytest.mark.parametrize("make", MAKES)
def test_a_made_up_path_fails(repo, make):
    claim = make(repo)
    start = git(repo, "rev-parse", "HEAD")
    assert gate(start, claim).violations == [f"{claim}: {MISSING}"]


def test_a_file_removed_before_the_start_commit_fails(repo):
    git(repo, "rm", "-q", "gone.txt")
    git(repo, "commit", "-qm", "rm")
    start = git(repo, "rev-parse", "HEAD")
    assert gate(start, "gone.txt").violations == [f"gone.txt: {MISSING}"]


def test_git_s_quoted_spelling_fails(repo):
    (repo / "é.txt").write_text("e\n")
    git(repo, "add", "é.txt")
    git(repo, "commit", "-qm", "e")
    start = git(repo, "rev-parse", "HEAD")
    (repo / "é.txt").unlink()
    claim = '"\\303\\251.txt"'
    assert gate(start, claim).violations == [f"{claim}: {MISSING}"]
    assert gate(start, "é.txt").passed


@pytest.mark.parametrize("how", ["commit", "commit-tree"])
def test_commit_then_remove_fails(repo, start, how):
    (repo / "fake.txt").write_text("fake\n")
    git(repo, "add", "fake.txt")
    if how == "commit":
        git(repo, "commit", "-qm", "fake")
    else:
        tree = git(repo, "write-tree")
        new = git(repo, "commit-tree", tree, "-p", "HEAD", "-m", "fake")
        git(repo, "update-ref", "HEAD", new)
    (repo / "fake.txt").unlink()
    assert gate(start, "fake.txt").violations == [f"fake.txt: {MISSING}"]


def test_delete_then_commit_passes(repo, start):
    git(repo, "rm", "-q", "gone.txt")
    git(repo, "commit", "-qm", "rm")
    report = gate(start, "gone.txt")
    assert report.passed, report.violations
    assert notes(report) == {"gone.txt": DELETED}


def _moves(start, other):
    return {
        "update-ref HEAD": [("update-ref", "HEAD", other)],
        "update-ref main": [("update-ref", "refs/heads/main", other)],
        "no-deref HEAD": [("update-ref", "--no-deref", "HEAD", other)],
        "symbolic-ref": [("symbolic-ref", "HEAD", "refs/heads/elsewhere")],
        "replace commit": [("replace", start, other)],
        "replace tree": [("replace", f"{start}^{{tree}}", f"{other}^{{tree}}")],
        "branch and tag named as the id": [("update-ref", f"refs/heads/{start}", other),
                                           ("update-ref", f"refs/tags/{start}", other)],
    }


@pytest.mark.parametrize("move", list(_moves("s", "o")))
def test_a_moved_head_changes_nothing(repo, start, move):
    (repo / "fake.txt").write_text("fake\n")
    git(repo, "add", "fake.txt")
    git(repo, "commit", "-qm", "fake")
    other = git(repo, "rev-parse", "HEAD")
    git(repo, "reset", "-q", "--hard", start)
    (repo / "gone.txt").unlink()
    claims = ("gone.txt", "fake.txt", "keep.txt", "never.txt")

    def verdicts():
        return [(c.item, c.ok, c.note) for c in gate(start, *claims).checks]
    before = verdicts()
    assert [v[1] for v in before] == [True, False, True, False]
    for args in _moves(start, other)[move]:
        git(repo, *args)
    if move.startswith("replace"):
        assert "fake.txt" in git(repo, "ls-tree", "-r", "--name-only", start).split("\n")
    assert verdicts() == before


@pytest.mark.parametrize("name", ["with space.txt", "é.txt", "new\nline.txt"])
def test_odd_names_pass(repo, name):
    (repo / name).write_text("x\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "odd")
    start = git(repo, "rev-parse", "HEAD")
    (repo / name).unlink()
    report = gate(start, name)
    assert report.passed, report.violations
    assert notes(report) == {name: DELETED}


def test_one_read_per_call(repo, start, box):
    (repo / "gone.txt").unlink()
    (repo / "keep.txt").unlink()
    report = gate(start, "gone.txt", "made_up.txt", "keep.txt")
    assert report.violations == [f"made_up.txt: {MISSING}"]
    assert len(box.runs()) == 1


@pytest.mark.parametrize("with_start", [True, False])
def test_all_existing_claims_run_no_git(repo, start, box, with_start):
    claims = BuildOutput(status="success", changed_files=["keep.txt", "src/a.txt"])
    report = (gates.diff_matches_claims(claims, None, start=start) if with_start
              else gates.diff_matches_claims(claims, None))
    assert report.passed
    assert box.runs() == []
    assert [c[0] for c in box.calls] == ["exists", "size", "exists", "size"]


def test_the_exact_command(repo, start, box):
    (repo / "gone.txt").unlink()
    gate(start, "gone.txt")
    (kind, cmd, kw), = box.runs()
    assert cmd == command(start)
    assert kw.get("text") is False
    assert "cwd" not in kw or kw["cwd"] is None
    i = cmd.index("-c")
    assert tuple(cmd[i:i + 4]) == tuple(utils.GIT_NO_PROGRAMS)


@pytest.mark.parametrize("given", ["absent", None, "", "abc1234", "HEAD", "main", "--help"])
def test_no_start_commit_is_as_strict_as_before(repo, box, given):
    (repo / "gone.txt").unlink()
    claims = BuildOutput(status="success", changed_files=["gone.txt"])
    report = (gates.diff_matches_claims(claims, None) if given == "absent"
              else gates.diff_matches_claims(claims, None, start=given))
    assert report.violations == [f"gone.txt: {MISSING}"]
    assert box.runs() == []


def test_a_start_that_names_no_object_fails_as_before(repo, box):
    (repo / "gone.txt").unlink()
    assert gate("0" * 40, "gone.txt").violations == [f"gone.txt: {MISSING}"]
    assert len(box.runs()) == 1


def test_not_a_repo(tmp_path, monkeypatch, box):
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(plain)
    assert gate("a" * 40, "x.txt").violations == [f"x.txt: {MISSING}"]
    assert len(box.runs()) == 1


def _count(root: Path) -> int:
    return sum(len(f) for _, _, f in os.walk(root / ".git" / "objects"))


def test_a_partial_clone_fetches_nothing(tmp_path, monkeypatch, box):
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, "init", "-q")
    git(origin, "config", "uploadpack.allowFilter", "true")
    (origin / "f.txt").write_text("f\n")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "one")
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", "--filter=tree:0", "--no-checkout", f"file://{origin}", str(clone))
    monkeypatch.chdir(clone)
    start = git(clone, "rev-parse", "HEAD")
    before = _count(clone)
    report = gate(start, "f.txt")
    assert report.violations == [f"f.txt: {MISSING}"]
    assert _count(clone) == before


class Vm:
    """Not Local: every path is missing, and the command is answered by the VM."""

    name = "vm"

    def __init__(self):
        self.commands = []

    def exists(self, path):
        return False

    def size(self, path):
        raise AssertionError("size on a missing path")

    def run(self, command, **kw):
        self.commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, b"gone.txt\0", b"")


def test_the_vm_is_asked_not_the_host(repo, start, monkeypatch):
    vm = Vm()
    monkeypatch.setattr(sandbox, "_current", vm)
    report = gate(start, "gone.txt", "other.txt")
    assert (repo / "gone.txt").exists()
    assert notes(report)["gone.txt"] == DELETED
    assert report.violations == [f"other.txt: {MISSING}"]
    assert vm.commands == [command(start)]


def test_no_planted_program_runs(repo, start):
    marker = repo.parent / "marker"
    script = repo.parent / "mark.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\ncat\n")
    script.chmod(0o755)
    git(repo, "config", "core.fsmonitor", str(script))
    git(repo, "config", "filter.mark.clean", str(script))
    (repo / ".gitattributes").write_text("* filter=mark\n")
    hooks = repo / ".git" / "hooks"
    for h in ("pre-commit", "post-index-change"):
        (hooks / h).write_text(f"#!/bin/sh\ntouch {marker}\n")
        (hooks / h).chmod(0o755)
    (repo / "keep.txt").write_text("changed\n")
    (repo / "gone.txt").unlink()
    index = (repo / ".git" / "index").read_bytes()
    report = gate(start, "keep.txt", "gone.txt")
    assert report.passed, report.violations
    assert not marker.exists()
    assert (repo / ".git" / "index").read_bytes() == index


# ── agents.execute hands the gate the commit the call started from ────────────

def _answer(files):
    return json.dumps({"status": "success", "summary": "done", "changed_files": files})


def test_execute_hands_over_the_start_commit(world):
    seen = {"spy": [], "two": 0}

    def spy(envelope, run, start=None):
        seen["spy"].append(start)
        return []

    def two(envelope, run):
        seen["two"] += 1
        return []

    def turn(n, kw):
        write(world, "specs/new.md", "n\n")
        rgit(world.repo, "add", "-A")
        rgit(world.repo, "commit", "-qm", "agent")
        return AgentResult(text=_answer(["specs/new.md"]), cost=0.1)
    world.turn = turn
    world.gates = [spy, two]
    world.execute()
    assert seen["spy"] == [world.base]
    assert seen["two"] == 1


def test_execute_judges_before_the_commits_are_undone(world):
    verdicts = []

    def wrapper(envelope, run, start=None):
        verdicts.append(world.calls["undo"])
        return gates.diff_matches_claims(envelope, run, start=start)
    wrapper.__name__ = "diff_matches_claims"

    def turn(n, kw):
        write(world, "fake.txt", "fake\n")
        rgit(world.repo, "add", "-A")
        rgit(world.repo, "commit", "-qm", "agent")
        (world.repo / "fake.txt").unlink()
        return AgentResult(text=_answer(["fake.txt"]), cost=0.1)
    world.turn = turn
    world.gates = [wrapper]
    with pytest.raises(agents.GateFailure) as raised:
        world.execute()
    assert f"fake.txt: {MISSING}" in str(raised.value)
    assert verdicts == [0]
    failed = world.named("diff_matches_claims")
    assert failed and failed[-1].type == "gate_fail"
    assert failed[-1].payload["violations"] == [f"fake.txt: {MISSING}"]


def test_execute_passes_a_real_deletion(world):
    write(world, "specs/old.md", "old\n")
    rgit(world.repo, "add", "-A")
    rgit(world.repo, "commit", "-qm", "old spec")

    def turn(n, kw):
        (world.repo / "specs" / "old.md").unlink()
        return AgentResult(text=_answer(["specs/old.md"]), cost=0.1)
    world.turn = turn
    world.gates = [gates.diff_matches_claims]
    world.execute()
    assert [e.type for e in world.named("diff_matches_claims")] == ["gate_pass"]


@pytest.mark.parametrize("role", ["builder", "ticket_builder"])
def test_the_prompts_say_what_to_list(role):
    text = (SKILL / "templates" / "prompt_engineering" / role / "user.md").read_text()
    assert ("`changed_files` lists every path your change touched, from the repo root, as git names it: "
            "each file you added or edited, each file you deleted, and both the old and the new path "
            "of a rename.") in text
