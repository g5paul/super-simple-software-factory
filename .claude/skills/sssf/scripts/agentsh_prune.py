"""Prune agentsh's event store to the last 2 days. Runs as root from sssf-agentsh-prune.service.

Standard library only. It deletes nothing and runs no ANALYZE while the service lists a session or
doesn't answer. It writes a status file that anyone can read: counts and times, never event content.
"""
import json
import os
import sqlite3
import stat
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

DB = "/var/lib/agentsh/events.db"            # agentsh_sssf.yaml audit.storage.sqlite_path
API = "http://127.0.0.1:18080"               # agentsh_guard.API
STATUS = "/var/lib/sssf-agentsh-prune/status.json"   # agentsh_guard.PRUNE_STATUS
KEEP_SECONDS = 2 * 24 * 3600
PRUNE_EVERY = 3600         # the timer looks every 10 minutes; a prune is due an hour after the last
ANALYZE_SECONDS = 7 * 24 * 3600
BATCH = 5000
LOCK_WAIT = 5.0            # sqlite3.connect(timeout=...)
PAUSE = 0.05               # seconds between batches


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def sessions(api):
    """How many sessions the service lists. Raises on any reply it can't read."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"{api}/api/v1/sessions", timeout=5) as reply:
        if reply.status != 200:
            raise RuntimeError(f"HTTP {reply.status}")
        body = json.loads(reply.read())
    if body is None:
        return 0
    if isinstance(body, dict):
        if "sessions" not in body:
            raise TypeError("sessions reply has no sessions list")
        body = body["sessions"]
        if body is None:
            raise TypeError("sessions reply has a null sessions list")
    if not isinstance(body, list):
        raise TypeError("sessions reply is not a list")
    return len(body)


def store_bytes(db):
    try:
        size = os.stat(db).st_size
    except OSError:
        return None
    try:
        size += os.stat(db + "-wal").st_size
    except OSError:
        pass
    return size


def write_status(path, data):
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o755, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=folder, prefix=".status-")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
        os.chmod(temp, 0o644)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def fix_owner(db):
    """Give a -wal or -shm file that root's connection made back to the database's owner."""
    try:
        owner = os.lstat(db)
    except OSError:
        return
    for path in (db + "-wal", db + "-shm"):
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        if info.st_uid != owner.st_uid:
            os.chown(path, owner.st_uid, owner.st_gid, follow_symlinks=False)


def skip_reason(api):
    """None when no session is listed, else why the prune must not run."""
    try:
        count = sessions(api)
    except Exception as error:
        return f"the service didn't answer its sessions list ({type(error).__name__})"
    if count > 0:
        return f"the service reports {count} active session(s)"
    return None


def prune(db=DB, api=API, status=STATUS, now=None):
    now = time.time() if now is None else now
    try:
        with open(status) as handle:
            old = json.load(handle)
        if not isinstance(old, dict):
            old = {}
    except (OSError, ValueError):
        old = {}
    data = {"store_bytes": None,
            "last_prune": old.get("last_prune"),
            "last_analyze": old.get("last_analyze"),
            "last_run": iso(now),
            "deleted": old.get("deleted") if isinstance(old.get("deleted"), int) else 0,
            "skipped": old.get("skipped"),
            "error": old.get("error")}
    last = parse(data["last_prune"])
    if last is not None and now - last < PRUNE_EVERY:
        data["store_bytes"] = store_bytes(db)
        write_status(status, data)
        return data
    reason = skip_reason(api)
    conn = None
    deleted = 0
    try:
        if reason is None:
            conn = sqlite3.connect(db, timeout=LOCK_WAIT, isolation_level=None)
            cutoff = int((now - KEEP_SECONDS) * 1e9)
            while True:
                reason = skip_reason(api)
                if reason:
                    break
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    cursor = conn.execute(
                        "DELETE FROM events WHERE rowid IN "
                        "(SELECT rowid FROM events ORDER BY rowid LIMIT ?) AND ts_unix_ns < ?", (BATCH, cutoff))
                    count = cursor.rowcount
                    conn.execute("COMMIT")
                except BaseException:
                    if conn.in_transaction:
                        conn.execute("ROLLBACK")
                    raise
                deleted += count
                if count < BATCH:
                    break
                time.sleep(PAUSE)
            if reason is None:
                last_analyze = parse(data["last_analyze"])
                if last_analyze is None or now - last_analyze >= ANALYZE_SECONDS:
                    reason = skip_reason(api)
                    if reason is None:
                        conn.execute("PRAGMA analysis_limit=1000")
                        conn.execute("ANALYZE")
                        data["last_analyze"] = iso(now)
        if reason is None:
            data["last_prune"] = iso(now)
            data["deleted"] = deleted
            data["skipped"] = None
            data["error"] = None
        else:
            data["skipped"] = reason
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as error:
        data["error"] = f"{type(error).__name__}: {error}"
    finally:
        if conn is not None:
            conn.close()
    try:
        fix_owner(db)
    except OSError as error:
        data["error"] = f"{type(error).__name__}: {error}"
    data["store_bytes"] = store_bytes(db)
    write_status(status, data)
    return data


def main():
    prune()
    return 0


if __name__ == "__main__":
    sys.exit(main())
