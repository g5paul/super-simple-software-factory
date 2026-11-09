"""Write files under the session folder without following an agent's links.

An agent can place symlinks, hard links, FIFOs and folder links under
`<data_root>/sessions/`. `Path.write_text` and `open("a")` follow them, so a
host write could land in a file the host holds open or in an engineer file.
A rename replaces a link and never follows it. Every host write under the
session folder must come through here, with the run's data root passed
explicitly. The helper never reads a link: it replaces it.
"""

from __future__ import annotations

import errno
import fcntl
import os
import secrets
import stat
from typing import TextIO

_TRIES = 3
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class LinkedPath(OSError):
    """The helper could not make the path its own after a few tries."""


def _walk(root, path) -> tuple[int, str]:
    """Open the folder holding `path`, one component at a time, never following links.

    Returns the folder's descriptor and the file name. The caller closes the fd.
    """
    if root is None:
        raise ValueError("session_files needs the run's data root, got None")
    root_abs = os.path.abspath(os.fspath(root))
    path_abs = os.path.abspath(os.fspath(path))
    if path_abs == root_abs or not path_abs.startswith(root_abs.rstrip(os.sep) + os.sep):
        raise ValueError(f"{path_abs} is not under the data root {root_abs}")
    parts = path_abs[len(root_abs.rstrip(os.sep)) + 1 :].split(os.sep)
    name = parts[-1]
    fd = os.open(root_abs, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in parts[:-1]:
            child = _open_dir(fd, part, path_abs)
            os.close(fd)
            fd = child
    except BaseException:
        os.close(fd)
        raise
    return fd, name


def _open_dir(parent: int, part: str, full: str) -> int:
    for _ in range(_TRIES):
        try:
            return os.open(part, _DIR_FLAGS, dir_fd=parent)
        except FileNotFoundError:
            try:
                os.mkdir(part, dir_fd=parent)
            except FileExistsError:
                pass
        except OSError as e:
            if e.errno not in (errno.ELOOP, errno.ENOTDIR):
                raise
            try:
                os.unlink(part, dir_fd=parent)
            except FileNotFoundError:
                pass
            try:
                os.mkdir(part, dir_fd=parent)
            except FileExistsError:
                pass
    raise LinkedPath(f"could not make {full} its own folder")


def write(path, data: str | bytes, root) -> None:
    """Write `data` to `path` by temp file and rename, replacing whatever is there."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    dfd, name = _walk(root, path)
    tmp = f".sssf.{os.getpid()}.{secrets.token_hex(8)}.tmp"   # length independent of the target name
    try:
        try:
            fd = os.open(
                tmp,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o666,
                dir_fd=dfd,
            )
            try:
                view = memoryview(raw)
                while view:
                    view = view[os.write(fd, view) :]
            finally:
                os.close(fd)
            os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            try:
                os.unlink(tmp, dir_fd=dfd)
            except OSError:
                pass
            raise
    finally:
        os.close(dfd)


def open_append(path, root) -> TextIO:
    """Open `path` for append, keeping only a regular single-link file of ours."""
    dfd, name = _walk(root, path)
    try:
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        for _ in range(_TRIES):
            try:
                fd = os.open(name, flags, 0o666, dir_fd=dfd)
            except OSError as e:
                if e.errno not in (errno.ELOOP, errno.ENXIO):
                    raise
                try:
                    os.unlink(name, dir_fd=dfd)
                except FileNotFoundError:
                    pass
                flags |= os.O_EXCL
                continue
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                os.close(fd)
                try:
                    os.unlink(name, dir_fd=dfd)
                except FileNotFoundError:
                    pass
                flags |= os.O_EXCL
                continue
            fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
            return os.fdopen(fd, "a", encoding="utf-8")
        raise LinkedPath(f"could not make {os.path.abspath(os.fspath(path))} its own file")
    finally:
        os.close(dfd)
