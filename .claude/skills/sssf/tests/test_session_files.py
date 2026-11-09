"""session_files: an agent's link under the session folder is replaced, never followed."""

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "templates" / "adws"))

from adw_modules import session_files  # noqa: E402


@pytest.fixture
def where(tmp_path):
    root = tmp_path / "data"
    folder = root / "sessions" / "abcd1234" / "planner"
    folder.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    return root, folder, outside


def test_write_over_a_symlink_replaces_it(where):
    root, folder, outside = where
    target = outside / "victim.txt"
    target.write_text("keep")
    (folder / "envelope.json").symlink_to(target)
    session_files.write(folder / "envelope.json", "new", root)
    assert target.read_text() == "keep"
    assert not (folder / "envelope.json").is_symlink()
    assert (folder / "envelope.json").read_text() == "new"


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="no /proc/self/fd")
@pytest.mark.parametrize("prefix", ["/dev/fd", "/proc/self/fd"])
def test_write_over_a_descriptor_link_leaves_the_open_file(where, prefix):
    root, folder, outside = where
    held = outside / "held.txt"
    held.write_text("held-bytes")
    with open(held, "r+") as handle:
        (folder / "envelope.json").symlink_to(f"{prefix}/{handle.fileno()}")
        session_files.write(folder / "envelope.json", "new", root)
    assert held.read_text() == "held-bytes"
    assert (folder / "envelope.json").read_text() == "new"


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="no /proc/self/fd")
def test_write_over_a_link_to_an_open_sqlite_db_keeps_the_db(where):
    root, folder, outside = where
    db = outside / "sssf.db"
    conn = sqlite3.connect(db)
    conn.execute("create table t (v text)")
    conn.execute("insert into t values ('row')")
    conn.commit()
    (folder / "envelope.json").symlink_to(f"/proc/self/fd/{_fd(db)}")
    session_files.write(folder / "envelope.json", "new", root)
    conn.close()
    assert sqlite3.connect(db).execute("select v from t").fetchall() == [("row",)]


def _fd(path: Path) -> int:
    """A descriptor this process holds open on `path` (sqlite's own, found in /proc)."""
    for name in os.listdir("/proc/self/fd"):
        try:
            if os.path.samefile(f"/proc/self/fd/{name}", path):
                return int(name)
        except OSError:
            continue
    raise AssertionError("no open descriptor")


def test_write_over_a_hard_link_leaves_the_other_name(where):
    root, folder, outside = where
    target = outside / "victim.txt"
    target.write_text("keep")
    os.link(target, folder / "envelope.json")
    session_files.write(folder / "envelope.json", "new", root)
    assert target.read_text() == "keep"
    assert (folder / "envelope.json").read_text() == "new"


def test_open_append_replaces_a_symlink_a_hard_link_and_a_fifo(where):
    root, folder, outside = where
    victim = outside / "victim.txt"
    victim.write_text("keep")
    cases = {"sym": lambda p: p.symlink_to(victim),
             "hard": lambda p: os.link(victim, p),
             "fifo": lambda p: os.mkfifo(p)}
    for name, plant in cases.items():
        path = folder / f"{name}.jsonl"
        plant(path)
        with session_files.open_append(path, root) as handle:
            handle.write("line\n")
        assert victim.read_text() == "keep"
        assert path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1
        assert path.read_text() == "line\n"


def test_open_append_keeps_an_existing_regular_file(where):
    root, folder, _ = where
    path = folder / "raw.jsonl"
    path.write_text("one\n")
    with session_files.open_append(path, root) as handle:
        handle.write("two\n")
    assert path.read_text() == "one\ntwo\n"


@pytest.mark.parametrize("link", ["planner", "sessions"])
def test_a_folder_link_is_replaced_by_a_folder(where, link):
    root, folder, outside = where
    if link == "planner":
        swapped = folder
    else:
        swapped = root / "sessions"
    moved = swapped.with_name(swapped.name + "-moved")
    swapped.rename(moved)
    swapped.symlink_to(outside)
    session_files.write(folder / "envelope.json", "new", root)
    assert list(outside.iterdir()) == []
    assert not swapped.is_symlink() and swapped.is_dir()
    assert (folder / "envelope.json").read_text() == "new"


def test_a_path_outside_the_root_or_no_root_is_refused(where, tmp_path):
    root, folder, outside = where
    with pytest.raises(ValueError):
        session_files.write(outside / "x", "a", root)
    with pytest.raises(ValueError):
        session_files.open_append(outside / "x", root)
    with pytest.raises(ValueError):
        session_files.write(folder / "x", "a", None)


def test_a_failed_write_leaves_no_temp_file(where):
    root, folder, _ = where
    (folder / "envelope.json").mkdir()
    with pytest.raises(OSError):
        session_files.write(folder / "envelope.json", "new", root)
    assert [p.name for p in folder.iterdir()] == ["envelope.json"]


def test_a_target_name_at_the_limit_still_writes(where):
    root, folder, _ = where
    name = "b" * 255
    session_files.write(folder / name, "x", root)
    assert (folder / name).read_text() == "x"
    assert [p.name for p in folder.iterdir()] == [name]
