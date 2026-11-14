"""Prune agentsh's event store and its rotated audit log to the last 2 hours. Runs as root from
sssf-agentsh-prune.service.

Standard library only. It deletes nothing and runs no ANALYZE while the service lists a live session
or doesn't answer; the audit step skips the same way. A session started and last heard from more than
KEEP_SECONDS ago is stale, such as one a cut-short probe left: it has no event the prune would keep, so it
doesn't hold the prune off. It never runs VACUUM: agentsh sets no busy timeout and drops a batch of events
whose write meets a lock, and a run has no session listed between its agent calls. It writes a status
file that anyone can read: counts and times, never event content.

Exit codes: 0 after a prune, a skip or "not due"; 1 when this attempt recorded an error (so
`systemctl --failed` lists the service). A skip exits 0 even if the status file still holds an old error.
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
AUDIT = "/var/lib/agentsh/audit.jsonl"      # agentsh_sssf.yaml audit.output
KEEP_SECONDS = 2 * 3600
# The timer looks every 10 minutes. A prune is due once the last is at least PRUNE_EVERY - PRUNE_SLACK
# old, so a check that lands a second short of the hour still prunes. Because of that bound, prunes
# never come less than 50 minutes apart; they land at the 50- or 60-minute check, by the timer's jitter.
# last_prune is stored in whole seconds, rounded down, so the bound is 3,000 s give or take under a second.
PRUNE_EVERY = 3600
PRUNE_SLACK = 600          # one timer period (OnCalendar=*:0/10)
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
    """The sessions the service lists. Raises on any reply it can't read."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"{api}/api/v1/sessions", timeout=5) as reply:
        if reply.status != 200:
            raise RuntimeError(f"HTTP {reply.status}")
        body = json.loads(reply.read())
    if body is None:
        return []
    if isinstance(body, dict):
        if "sessions" not in body:
            raise TypeError("sessions reply has no sessions list")
        body = body["sessions"]
        if body is None:
            raise TypeError("sessions reply has a null sessions list")
    if not isinstance(body, list):
        raise TypeError("sessions reply is not a list")
    return body


def stale(session, db, now):
    """True only for a session that agentsh started more than KEEP_SECONDS ago and whose last event in the
    store is older than that too. Anything it can't read counts as live."""
    if not isinstance(session, dict) or not isinstance(session.get("id"), str) or not session["id"]:
        return False
    started = parse(session.get("created_at"))
    if started is None or now - started <= KEEP_SECONDS:
        return False
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=LOCK_WAIT)
        try:
            last = conn.execute("SELECT MAX(ts_unix_ns) FROM events WHERE session_id = ?",
                                (session["id"],)).fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    return last is None or last / 1e9 < now - KEEP_SECONDS


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


def open_folder(audit):
    """An fd on the audit log's folder. Raises OSError when the folder is a link, missing or not a directory,
    so nothing is listed or deleted through a link."""
    return os.open(os.path.dirname(audit), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def rotated(audit, fd):
    """(name, lstat) of each regular file named audit.<N>, N one or more ASCII digits, beside the live log.
    Works on bare names in the folder the fd holds."""
    prefix = os.path.basename(audit) + "."
    found = []
    for name in os.listdir(fd):
        if not name.startswith(prefix):
            continue
        suffix = name[len(prefix):]
        if not (suffix.isascii() and suffix.isdigit()):
            continue
        try:
            info = os.lstat(name, dir_fd=fd)
        except FileNotFoundError:
            continue
        if stat.S_ISREG(info.st_mode):
            found.append((name, info))
    return found


def audit_bytes(audit):
    """Size of the live audit log and its rotated files, or None when the folder is a link or can't be listed."""
    try:
        fd = open_folder(audit)
        try:
            files = rotated(audit, fd)
            total = sum(info.st_size for _, info in files)
            try:
                live = os.lstat(os.path.basename(audit), dir_fd=fd)
                if stat.S_ISREG(live.st_mode):
                    total += live.st_size
            except FileNotFoundError:
                pass
        finally:
            os.close(fd)
    except OSError:
        return None
    return total


def prune_audit(audit, now):
    """Delete rotated audit files last modified more than KEEP_SECONDS ago. Never the live log.
    Deletes nothing when the folder is a link."""
    fd = open_folder(audit)
    try:
        for name, info in rotated(audit, fd):
            if now - info.st_mtime > KEEP_SECONDS:
                try:
                    os.unlink(name, dir_fd=fd)
                except FileNotFoundError:
                    pass
    finally:
        os.close(fd)


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


def skip_reason(api, db, now):
    """None when every listed session is stale, else why the prune must not run."""
    try:
        listed = sessions(api)
    except Exception as error:
        return f"the service didn't answer its sessions list ({type(error).__name__})"
    live = sum(1 for each in listed if not stale(each, db, now))
    if live:
        return f"the service reports {live} active session(s)"
    return None


def _prune(db, api, status, now, audit):
    """One prune attempt. Returns (status data, whether this attempt recorded an error)."""
    errored = False
    now = time.time() if now is None else now
    try:
        with open(status) as handle:
            old = json.load(handle)
        if not isinstance(old, dict):
            old = {}
    except (OSError, ValueError):
        old = {}
    data = {"store_bytes": None,
            "audit_bytes": None,
            "last_prune": old.get("last_prune"),
            "last_analyze": old.get("last_analyze"),
            "last_run": iso(now),
            "deleted": old.get("deleted") if isinstance(old.get("deleted"), int) else 0,
            "skipped": old.get("skipped"),
            "error": old.get("error")}
    last = parse(data["last_prune"])
    if last is not None and now - last < PRUNE_EVERY - PRUNE_SLACK:
        data["store_bytes"] = store_bytes(db)
        data["audit_bytes"] = audit_bytes(audit)
        write_status(status, data)
        return data, False
    reason = skip_reason(api, db, now)
    conn = None
    deleted = 0
    try:
        if reason is None:
            conn = sqlite3.connect(db, timeout=LOCK_WAIT, isolation_level=None)
            cutoff = int((now - KEEP_SECONDS) * 1e9)
            while True:
                reason = skip_reason(api, db, now)
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
                    reason = skip_reason(api, db, now)
                    if reason is None:
                        conn.execute("PRAGMA analysis_limit=1000")
                        conn.execute("ANALYZE")
                        data["last_analyze"] = iso(now)
            if reason is None:
                reason = skip_reason(api, db, now)
                if reason is None:
                    prune_audit(audit, now)
        if reason is None:
            data["last_prune"] = iso(now)
            data["deleted"] = deleted
            data["skipped"] = None
            data["error"] = None
        else:
            data["skipped"] = reason
    except (sqlite3.OperationalError, sqlite3.DatabaseError, OSError) as error:
        data["error"] = f"{type(error).__name__}: {error}"
        errored = True
    finally:
        if conn is not None:
            conn.close()
    try:
        fix_owner(db)
    except OSError as error:
        data["error"] = f"{type(error).__name__}: {error}"
        errored = True
    data["store_bytes"] = store_bytes(db)
    data["audit_bytes"] = audit_bytes(audit)
    write_status(status, data)
    return data, errored


def prune(db=DB, api=API, status=STATUS, now=None, audit=AUDIT):
    return _prune(db, api, status, time.time() if now is None else now, audit)[0]


def main():
    _, errored = _prune(DB, API, STATUS, time.time(), AUDIT)
    return 1 if errored else 0


if __name__ == "__main__":
    sys.exit(main())
