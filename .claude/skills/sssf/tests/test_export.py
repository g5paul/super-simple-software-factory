"""Export: a feature's product changes on a branch based on upstream, no SSSF files.

Each test makes a throwaway upstream (a bare repo) and a fork cloned from it,
with SSSF installed on the fork's main and a feature on top, then runs
adw_export.main() in the fork and reads the branch it built.
Design: docs/designs/upstream-patches.md, decisions 7 to 11 and 24 to 26.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

import adw_export


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          check=True).stdout.strip()


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def commit(root: Path, message: str, files: dict[str, str]) -> str:
    for path, text in files.items():
        write(root, path, text)
    git("add", "-A", cwd=root)
    git("commit", "-q", "-m", message, cwd=root)
    return git("rev-parse", "HEAD", cwd=root)


def files_at(root: Path, ref: str) -> set[str]:
    return set(git("ls-tree", "-r", "--name-only", ref, cwd=root).splitlines())


def subjects(root: Path, ref: str, onto: str = "upstream/main") -> list[str]:
    return git("log", "--reverse", "--format=%s", f"{onto}..{ref}", cwd=root).splitlines()


@pytest.fixture
def fork(tmp_path, monkeypatch):
    """A fork with SSSF installed on main, and upstream/main as the clean base."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git("init", "-q", "-b", "main", cwd=seed)
    for key, value in (("user.email", "t@example.com"), ("user.name", "T")):
        git("config", key, value, cwd=seed)
    commit(seed, "upstream: first", {"pkg/calc.py": "def add(a, b):\n    return a + b\n",
                                     "specs/notes.md": "the project's own spec folder\n",
                                     "README.md": "calc\n"})
    upstream = tmp_path / "upstream.git"
    git("clone", "-q", "--bare", str(seed), str(upstream), cwd=tmp_path)

    root = tmp_path / "fork"
    git("clone", "-q", str(upstream), str(root), cwd=tmp_path)
    for key, value in (("user.email", "t@example.com"), ("user.name", "T")):
        git("config", key, value, cwd=root)
    git("remote", "add", "upstream", str(upstream), cwd=root)
    git("fetch", "-q", "upstream", cwd=root)
    commit(root, "Install SSSF", {"adws/adw_modules/x.py": "x = 1\n",
                                  "adws/adw_sssf_config/repo.yaml": "merge: any\n",
                                  ".claude/skills/sssf/SKILL.md": "skill\n",
                                  "sssf.just": "recipes\n",
                                  ".gitignore": "adws/adw_data/\n"})
    monkeypatch.chdir(root)
    return root


def test_a_branch_exports_its_product_change_without_its_spec(fork, capsys):
    git("checkout", "-q", "-b", "feat", cwd=fork)
    commit(fork, "Add sub", {"pkg/calc.py": "def add(a, b):\n    return a + b\n\n\n"
                                            "def sub(a, b):\n    return a - b\n",
                             "specs/a1b2c3d4_add-sub.md": "# Plan\n"})

    assert adw_export.main(["feat"]) == 0

    assert subjects(fork, "export/feat") == ["Add sub"]
    files = files_at(fork, "export/feat")
    assert "specs/a1b2c3d4_add-sub.md" not in files
    assert "def sub" in git("show", "export/feat:pkg/calc.py", cwd=fork)
    assert files == {"pkg/calc.py", "specs/notes.md", "README.md"}


@pytest.mark.parametrize("path, sssf", [
    ("adws/adw_modules/permissions.py", True),
    ("adws/adw_data/prompt_engineering/builder/system.md", True),
    (".claude/skills/sssf/SKILL.md", True),
    (".scratch/add-sub/tickets.md", True),
    (".githooks/pre-push", True),
    ("sssf.just", True),
    ("docs/sssf.md", True),
    ("specs/a1b2c3d4_add-sub.md", True),
    ("specs/a1b2c3d4_add-sub_v2.md", True),
    ("app_docs/a1b2c3d4_add-sub.md", True),
    # The project's own files, under the same names or folders.
    ("specs/notes.md", False),
    ("specs/design/a1b2c3d4_x.md", False),
    ("app_docs/guide.md", False),
    ("docs/usage.md", False),
    (".claude/skills/other/SKILL.md", False),
    (".claude/settings.json", False),
    ("justfile", False),
    ("pkg/adws/x.py", False),
    ("pkg/calc.py", False),
])
def test_which_paths_are_sssf_files(path, sssf):
    assert adw_export.is_sssf_file(path) is sssf


def test_a_commit_left_empty_is_dropped_and_the_projects_own_spec_edit_kept(fork, capsys):
    git("checkout", "-q", "-b", "feat", cwd=fork)
    commit(fork, "Plan sub", {"specs/a1b2c3d4_add-sub.md": "# Plan\n"})
    commit(fork, "Add sub", {"pkg/calc.py": "def sub(a, b):\n    return a - b\n",
                             "specs/notes.md": "the project's own spec folder\nsub added\n"})
    commit(fork, "Document sub", {"app_docs/a1b2c3d4_add-sub.md": "# Sub\n",
                                  "adws/adw_data/x.json": "{}\n"})

    assert adw_export.main(["feat"]) == 0

    assert subjects(fork, "export/feat") == ["Add sub"]
    assert "sub added" in git("show", "export/feat:specs/notes.md", cwd=fork)
    out = capsys.readouterr().out
    assert "Plan sub" in out and "Document sub" in out and "dropped" in out


def feature(fork: Path) -> None:
    git("checkout", "-q", "-b", "feat", cwd=fork)
    commit(fork, "Add sub", {"pkg/calc.py": "def sub(a, b):\n    return a - b\n"})


def test_it_refuses_a_dirty_tree_and_builds_nothing(fork, capsys):
    feature(fork)
    write(fork, "pkg/calc.py", "half-done\n")

    assert adw_export.main(["feat"]) == 1

    assert "dirty" in capsys.readouterr().err
    assert git("branch", "--list", "export/*", cwd=fork) == ""


def test_an_existing_export_branch_is_kept_unless_replace(fork, capsys):
    feature(fork)
    assert adw_export.main(["feat"]) == 0
    first = git("rev-parse", "export/feat", cwd=fork)
    commit(fork, "Add mul", {"pkg/mul.py": "def mul(a, b):\n    return a * b\n"})

    assert adw_export.main(["feat"]) == 1
    assert "--replace" in capsys.readouterr().err
    assert git("rev-parse", "export/feat", cwd=fork) == first

    assert adw_export.main(["feat", "--replace"]) == 0
    assert subjects(fork, "export/feat") == ["Add sub", "Add mul"]


def test_a_commit_that_leans_on_a_fork_commit_stops_the_export(fork, capsys):
    commit(fork, "Fork-only fix", {"pkg/calc.py": "def add(a, b):\n    return b + a\n"})
    git("checkout", "-q", "-b", "feat", cwd=fork)
    commit(fork, "Tune add", {"pkg/calc.py": "def add(a, b):\n    return int(b + a)\n"})

    assert adw_export.main(["feat"]) == 1

    err = capsys.readouterr().err
    assert "Tune add" in err and "won't apply" in err
    assert git("branch", "--list", "export/*", cwd=fork) == ""
    assert len(git("worktree", "list", cwd=fork).splitlines()) == 1
    assert git("status", "--porcelain", cwd=fork) == ""


def merged_main(fork: Path, *, own_change: bool) -> None:
    git("checkout", "-q", "-b", "feat", cwd=fork)
    commit(fork, "Add sub", {"pkg/sub.py": "def sub(a, b):\n    return a - b\n"})
    git("checkout", "-q", "main", cwd=fork)
    commit(fork, "Update SSSF", {"adws/adw_modules/x.py": "x = 2\n"})
    git("checkout", "-q", "feat", cwd=fork)
    git("merge", "-q", "--no-ff", "--no-commit", "main", cwd=fork)
    if own_change:
        write(fork, "pkg/sub.py", "def sub(a, b):\n    return a - b  # merged\n")
        git("add", "-A", cwd=fork)
    git("commit", "-q", "-m", "Merge main into feat", cwd=fork)
    commit(fork, "Add mul", {"pkg/mul.py": "def mul(a, b):\n    return a * b\n"})


def test_a_merge_that_only_joins_history_is_skipped(fork, capsys):
    merged_main(fork, own_change=False)

    assert adw_export.main(["feat"]) == 0

    assert subjects(fork, "export/feat") == ["Add sub", "Add mul"]
    out = capsys.readouterr().out
    skipped = out[out.index("merges skipped"):]
    assert "Merge main into feat" in skipped


def test_a_merge_with_changes_of_its_own_stops_the_export(fork, capsys):
    merged_main(fork, own_change=True)

    assert adw_export.main(["feat"]) == 1

    assert "Merge main into feat" in capsys.readouterr().err
    assert git("branch", "--list", "export/*", cwd=fork) == ""


@pytest.mark.parametrize("line", ['exclude = ["adws/"]', "import? 'sssf.just'",
                                  "see .claude/skills/sssf", "see specs/a1b2c3d4_add-sub.md"])
def test_it_refuses_when_the_final_diff_names_an_sssf_path(fork, capsys, line):
    git("checkout", "-q", "-b", "feat", cwd=fork)
    commit(fork, "Add sub", {"pkg/calc.py": "def sub(a, b):\n    return a - b\n",
                             "pyproject.toml": f"[tool.ruff]\n{line}\n"})

    assert adw_export.main(["feat"]) == 1

    err = capsys.readouterr().err
    assert "pyproject.toml" in err and line in err
    assert git("branch", "--list", "export/*", cwd=fork) == ""


def test_a_removed_line_naming_sssf_is_fine(fork):
    seed = fork.parent / "seed"                   # upstream's own history has the line
    commit(seed, "Exclude", {"setup.cfg": "[flake8]\nexclude = adws/\n"})
    git("push", "-q", str(fork.parent / "upstream.git"), "main", cwd=seed)
    git("fetch", "-q", "upstream", cwd=fork)
    git("merge", "-q", "--no-edit", "upstream/main", cwd=fork)
    git("checkout", "-q", "-b", "feat", cwd=fork)
    commit(fork, "Drop exclude", {"setup.cfg": "[flake8]\n"})

    assert adw_export.main(["feat"]) == 0


def test_the_report_names_the_files_and_how_old_the_base_is(fork, capsys):
    feature(fork)
    before = git("rev-parse", "upstream/main", cwd=fork)

    assert adw_export.main(["feat"]) == 0

    out = capsys.readouterr().out
    assert "pkg/calc.py" in out[out.index("files:"):]
    date = git("log", "-1", "--format=%cs", "upstream/main", cwd=fork)
    assert f"upstream/main as of {date}" in out
    assert "git fetch upstream" in out
    assert git("rev-parse", "upstream/main", cwd=fork) == before
    assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=fork) == "feat"


def with_checks(fork: Path, checks: list[str]) -> None:
    import yaml
    commit(fork, "Set export checks",
           {"adws/adw_sssf_config/repo.yaml": yaml.safe_dump({"export_checks": checks})})


def test_without_export_checks_it_says_unchecked(fork, capsys):
    feature(fork)

    assert adw_export.main(["feat"]) == 0

    assert "unchecked" in capsys.readouterr().out


def test_export_checks_run_on_the_export_itself(fork, capsys):
    # Each check sees the exported tree: the feature's file, and no SSSF files.
    with_checks(fork, ["grep -q 'def sub' pkg/calc.py", "test ! -e adws"])
    feature(fork)

    assert adw_export.main(["feat"]) == 0

    out = capsys.readouterr().out
    assert "passed: grep -q 'def sub' pkg/calc.py" in out
    assert "passed: test ! -e adws" in out


def test_a_failing_check_keeps_the_branch_and_fails_the_export(fork, capsys):
    with_checks(fork, ["echo broken >&2; exit 3"])
    feature(fork)

    assert adw_export.main(["feat"]) == 1

    out = capsys.readouterr().out
    assert "failed (exit 3): echo broken" in out and "broken" in out
    assert subjects(fork, "export/feat") == ["Add sub"]


def test_a_ticket_feature_exports_its_own_commits_without_sssf_trailers(fork, capsys):
    commit(fork, "Spec add-sub\n\nSSSF-Feature: add-sub",
           {".scratch/add-sub/spec.md": "# Spec\n"})
    commit(fork, "Other work", {"pkg/other.py": "x = 1\n"})
    commit(fork, "Add sub\n\n# With a heading line.\n\nSSSF-Feature: add-sub\nSSSF-Step: ticket-1",
           {"pkg/calc.py": "def sub(a, b):\n    return a - b\n"})
    commit(fork, "Document add-sub\n\nSSSF-Feature: add-sub\nSSSF-Step: documented",
           {"app_docs/a1b2c3d4_add-sub.md": "# Sub\n"})

    assert adw_export.main(["--feature", "add-sub"]) == 0

    assert subjects(fork, "export/add-sub") == ["Add sub"]
    message = git("log", "-1", "--format=%B", "export/add-sub", cwd=fork)
    assert message == "Add sub\n\n# With a heading line."
    assert "pkg/other.py" not in files_at(fork, "export/add-sub")


def test_a_run_exports_the_commits_its_trace_records(fork, capsys):
    from adw_modules.data_types import EventRecord, SSSFConfig
    from adw_modules.tracer import Tracer

    cfg = SSSFConfig()
    tracer = Tracer(cfg.observability.db, f"{cfg.defaults.data_dir}/sessions/a1b2c3d4/events.jsonl",
                    data_root=cfg.defaults.data_dir)
    tracer.session_start("a1b2c3d4", "t", adw_name="adw_simple_sdlc")

    def traced(adw_id: str, message: str, files: dict[str, str]) -> None:
        sha = git("rev-parse", "--short", commit(fork, message, files), cwd=fork)
        tracer.event(EventRecord(adw_id=adw_id, phase_id="", type="log", name="commit_build",
                                 payload={"sha": sha, "message": message}))

    traced("a1b2c3d4", "Plan sub", {"specs/a1b2c3d4_add-sub.md": "# Plan\n"})
    traced("e5f6a7b8", "Another run", {"pkg/other.py": "x = 1\n"})
    traced("a1b2c3d4", "Add sub", {"pkg/calc.py": "def sub(a, b):\n    return a - b\n"})

    assert adw_export.main(["--run", "a1b2c3d4"]) == 0

    assert subjects(fork, "export/a1b2c3d4") == ["Add sub"]


def test_a_run_whose_commits_are_gone_is_named(fork, capsys):
    from adw_modules.data_types import EventRecord, SSSFConfig
    from adw_modules.tracer import Tracer

    cfg = SSSFConfig()
    tracer = Tracer(cfg.observability.db, f"{cfg.defaults.data_dir}/sessions/a1b2c3d4/events.jsonl",
                    data_root=cfg.defaults.data_dir)
    tracer.event(EventRecord(adw_id="a1b2c3d4", phase_id="", type="log", name="commit_build",
                             payload={"sha": "0badc0de", "message": "Add sub"}))

    assert adw_export.main(["--run", "a1b2c3d4"]) == 1

    assert "0badc0de" in capsys.readouterr().err


def test_a_run_whose_commits_are_on_no_branch_is_refused(fork, capsys):
    # After `just rebase` or `just discard`, the traced commits still exist as
    # objects but no branch holds them. Exporting them would send stale work.
    from adw_modules.data_types import EventRecord, SSSFConfig
    from adw_modules.tracer import Tracer

    cfg = SSSFConfig()
    tracer = Tracer(cfg.observability.db, f"{cfg.defaults.data_dir}/sessions/a1b2c3d4/events.jsonl",
                    data_root=cfg.defaults.data_dir)
    sha = git("rev-parse", "--short", commit(fork, "Add sub", {"pkg/sub.py": "x = 1\n"}), cwd=fork)
    tracer.event(EventRecord(adw_id="a1b2c3d4", phase_id="", type="log", name="commit_build",
                             payload={"sha": sha, "message": "Add sub"}))
    git("reset", "-q", "--hard", "HEAD^", cwd=fork)

    assert adw_export.main(["--run", "a1b2c3d4"]) == 1

    err = capsys.readouterr().err
    assert sha in err and "no branch" in err
    assert git("branch", "--list", "export/*", cwd=fork) == ""


def test_a_feature_of_sssf_files_only_builds_nothing(fork, capsys):
    git("checkout", "-q", "-b", "feat", cwd=fork)
    commit(fork, "Plan sub", {"specs/a1b2c3d4_add-sub.md": "# Plan\n"})

    assert adw_export.main(["feat"]) == 1

    assert "nothing to export" in capsys.readouterr().err
    assert git("branch", "--list", "export/*", cwd=fork) == ""


def test_an_added_line_starting_with_plus_signs_is_still_checked(fork, capsys):
    git("checkout", "-q", "-b", "feat", cwd=fork)
    commit(fork, "Add notes", {"pkg/notes.txt": "++ see adws/ for the factory\n"})

    assert adw_export.main(["feat"]) == 1

    assert "pkg/notes.txt:1" in capsys.readouterr().err
