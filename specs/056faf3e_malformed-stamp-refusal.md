# Refuse a malformed stamp record before `--update` writes anything

Item 4 of the Next list in `docs/designs/agentsh-local-sandbox.md`, found by run 8b800eb5
(`docs/designs/agentsh/guard-run-8b800eb5.md`).

The guard code here is `.claude/skills/sssf/scripts/install.py`, not a file under
`templates/adws/adw_modules/`: `--update` lives in the installer. No module under
`adw_modules/` reads `stamp.json` on the update path, so none changes.

## Threat model

- An agent runs as the engineer's own user, in the project's checkout. It can write
  `adws/adw_sssf_config/stamp.json` with any bytes. It can also replace it with a folder, a
  symlink, or delete it, and it can do this between runs. The engineer later runs
  `uv run .claude/skills/sssf/scripts/install.py --update` in that checkout, as themselves.
- `--update` treats the stamp as data. The agent's goal is to turn that data into a write,
  copy, delete or record the engineer did not intend. The fallback goal is a traceback partway
  through, which leaves the repo half-updated: templates copied but unrecorded, retired files
  still there, no registry entry.
- What this guard can't stop at its level: the agent can edit or delete any SSSF file in the
  repo directly, since it runs as the same user. A record that is forged but well formed can
  still have `--update` act on it, for example delete an in-repo SSSF file whose hash it
  matches. The installer can't tell an honest record from a forged one that has the same shape.
  This change narrows only the *shape* of what `--update` will act on.
- The agent may still be running while the engineer updates. The window between the check and
  the later reads is a race on `stamp.json`. It is closed by reading the file **once**: the
  check and every later use work from the same parsed `dict`, never from a second read.

## Baseline

`main` at `99ffb3a` ("Write the task for the update crash on a malformed stamp record"). Every
finding is judged against that commit.

On that baseline, `update()` calls `load_stamp()` (`json.loads` of the file, or
`{"files": {}}` when it is absent) and reads the result unchecked:

- **Before any write:** invalid JSON, a non-object top level, a missing or non-object `files`,
  a non-object `sssf.config.yaml` or `justfile` record, or an added-roster record without
  `source` crash with a traceback.
- **After the copies, before `save_stamp`:** a record without `sha256`, or a non-object one,
  crashes in the copy loop (`base["sha256"]`) or in `gone` (`entry["source"]`). A null-byte key
  crashes in `retire()`, because `os.open` raises `ValueError`, not `OSError`. Earlier copies
  are left in place unrecorded, and retired files sorted after the bad record stay.

## Bar

The change must:

1. Check the whole stamp in `update()` before the first write, copy, delete or record. The
   first write is `copy()` in the copy loop. `save_stamp`, `retire`, `ensure_gitignore` and
   `register` all come after it.
2. Refuse a malformed stamp: exit 1, write nothing, print no traceback. Name every bad record
   with its key escaped by `json.dumps` (pure ASCII, so `\u0000` and lone surrogates print
   safely). Tell the engineer to run
   `git checkout -- adws/adw_sssf_config/stamp.json`, check
   `git log -- adws/adw_sssf_config/stamp.json`, and run the update again.
3. Update a well-formed stamp exactly as before. The refusal must never fire on a stamp an
   install, `--add-roster` or `--update` wrote, on any skill version since `stamp.json` began
   (bc10bec). Its shape has been the same since then: `{"files": {rel: {"source": str,
   "sha256": <64 lowercase hex>}}, "skill"?: str}`, written by `json.dumps(..., indent=2,
   sort_keys=True)`, so it is ASCII. The roster templates have only ever been
   `sssf.config.yaml`, `sssf.config.claude_code.yaml` and `sssf.config.chatgpt.yaml`, all
   still shipped (checked with `git log --all -- '*sssf.config*.yaml'`).
4. Keep the absent-stamp path (installs from before `stamp.json`, the `old_repo` tests). It
   behaves as on baseline.

The change must never:

- Write, copy, delete or record anything, or register the repo, before it refuses.
- Crash with a traceback on any `stamp.json` contents.
- Catch exceptions around the update logic, add a repair or skip mode, or add a flag that turns
  the check off. The check is a narrowing: a stamp either passes it, and every later read is
  then safe by construction, or the update refuses. The only `except` the change adds is around
  the single read-and-parse of the file. There, an unreadable or unparseable file *is* the
  malformed case.
- Keep an agent file, run agent code, or delete or overwrite an engineer file where the
  baseline doesn't.

## Out of scope

Leave these holes open, and file them as old holes, not findings:

- A forged but well-formed record can still have `--update` delete an in-repo SSSF file whose
  hash it matches, or copy a starter roster to a forged `sssf.config.<name>.yaml` path. This is
  a known limit in `config.md` and stays there.
- A race in which the agent rewrites `stamp.json` after the single read. The update then
  overwrites the agent's version with `save_stamp`, which is the baseline behaviour.
- A symlinked or swapped `adws/adw_sssf_config/` folder.
- `install` (no `--update`) and `--add-roster` on a malformed stamp. They still call
  `load_stamp` unchecked, and both crash before writing on the shapes listed above.
- The version check in `agentsh_install.py`, which reads `skill` from the stamp.
- A FIFO or a huge file at a stamped path, or at `stamp.json` itself. Reading a FIFO there
  blocks, as on baseline.
- The policy rules, pi under the wrap, and sudo.

## The check: what a well-formed stamp is

Add to `.claude/skills/sssf/scripts/install.py`, next to `load_stamp`:

```python
CONFIG = "adws/adw_sssf_config/sssf.config.yaml"
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# Every roster template an install has ever recorded; all still ship.
STARTER_SOURCES = frozenset(p.relative_to(TEMPLATES).as_posix() for p in STARTERS.values())
```

and a function `read_stamp(root: Path) -> tuple[dict, list[str]]`. It returns the parsed
record and a list of problems, each a one-line ASCII string. It reads the file exactly once.

1. `os.lstat(root / STAMP)`:
   - `FileNotFoundError` returns `({"files": {}}, [])`. This is the absent-stamp path, as
     `load_stamp` has it.
   - Any other `OSError` is the problem `"stamp.json can't be read"`.
   - A mode that isn't `S_ISREG` (a folder, a symlink, a FIFO) is the problem
     `"stamp.json is not a regular file"`. A symlink is refused because `save_stamp` would
     otherwise write through it. No install writes one.
2. Read and parse once: `json.loads((root / STAMP).read_text(encoding="utf-8"))`. Catch
   `OSError`, `ValueError` (it covers `UnicodeDecodeError` and `json.JSONDecodeError`) and
   `RecursionError` (deeply nested arrays). Each becomes the problem
   `"stamp.json is not valid UTF-8 JSON"`. This is the one `except` the change adds.
3. The top level must be a `dict` (`"stamp.json is not a JSON object"`), and `files` must be
   present and a `dict` (`'stamp.json has no "files" object'`).
4. If `"skill"` is present, it must be a `str` that encodes as UTF-8
   (`'"skill" is not text'`). The summary prints it, and a lone surrogate would crash the print
   after `save_stamp`.
5. For each `rel, entry` in `sorted(files.items())`, collect every reason that applies. The
   record is named once, as `f"record {json.dumps(rel)}: {'; '.join(reasons)}"`:
   - The key must be a `str` (always true for JSON, but check it), contain no `"\x00"`
     (`"key holds a null byte"`), and `rel.encode("utf-8")` must succeed
     (`"key is not UTF-8 text"`). This covers lone surrogates, which `os.open` rejects with
     `UnicodeEncodeError`, a `ValueError`, and which crash the `gone` print.
   - `entry` must be a `dict` (`"not an object"`). When it isn't, skip the rest.
   - `entry.get("source")` must be a `str` (`'"source" missing or not text'`).
   - `entry.get("sha256")` must be a `str` with `SHA256_HEX.fullmatch(...)`
     (`'"sha256" missing or not 64 lowercase hex characters'`). Use `fullmatch`, not `match`
     with `$`, which accepts a trailing newline. Lowercase is what `hexdigest()` writes, so
     this is the narrower reading of "64 hex characters", and no written stamp fails it.
   - **Roster records:** when `rel == CONFIG` or `ADDED_ROSTER.match(rel)`, the same test
     `added_rosters` uses so the two can't drift, `source` must be in `STARTER_SOURCES`
     (`'"source" is not a starter roster'`). These are the only records whose `source`
     becomes a path (`TEMPLATES / source`) that the update reads. Any other source, such as a
     missing file, a folder, `../..`, or a null byte, crashes `sha256(src)` or `copy()` partway
     through the copy loop, after earlier copies. That is the same harm as the `sha256` case,
     so the bar requires it.
6. Return `(record, problems)`.

Do not change `load_stamp`. `install()`, `add_roster()` and `main()` keep using it (out of
scope).

## Every place `update()` and its helpers read the stamp, and why each is safe after the check

`update()` calls `read_stamp(root)` once, right after the "no SSSF install" check, which
writes nothing. If `problems` is non-empty, it prints the refusal and `return 1`. Every
line below runs after that call, on the checked `record`:

| Line on baseline | Read | Safe because |
|---|---|---|
| `install.py:430` `load_stamp(root)` | parse | replaced by `read_stamp`, which is the check |
| `:431` `record["files"]` | top-level `files` | rule 3 |
| `:435` `TEMPLATES / files[config]["source"]` | config record's `source`, used as a path | rules 5 (dict, str) and roster rule: it names a shipped starter |
| `:194` `has_container`: `rel.startswith` over keys | keys | rule 5: keys are `str` |
| `:162` `added_rosters`: `entry["source"]` for `ADDED_ROSTER` keys, then `targets()` and `sha256(src)` read it | added-roster `source`, used as a path | roster rule, using the same regex |
| `:442` `files.get("justfile", {}).get("source")` | justfile record | rule 5: every value is a `dict` |
| `:458` `files.get(rel)`, then `base["sha256"]` at `:464`, `:474`, `:482` (into `skill_change`) | `sha256` of records whose key is a target | rule 5: `sha256` present and a hex `str` |
| `:489` `entry["source"] not in sources` | every record's `source` | rule 5: present and `str` |
| `:491-495` `is_sssf_code(rel)`, `retire(root, rel, files[rel]["sha256"])`: `Path(rel).parts`, `os.open(part, dir_fd=…)`, `os.lstat(name, dir_fd=…)`, digest compare | key as a path, `sha256` | rule 5: no NUL and UTF-8 encodable, so `os.open` and `os.lstat` raise only `OSError`, which `retire` handles. `sha256` is a `str`. Every `is_sssf_code` key has at least one path part, so the unpack holds. |
| `:502` `save_stamp(root, record)` | whole record, through `json.dumps` | the record came from `json.loads`, and step 1 refused a symlink at the path |
| `:513` `record.get("skill")` printed | `skill` | rule 4 |
| `:557-559` `gone` keys printed | keys | rule 5: UTF-8 encodable |

`register()`, `ensure_gitignore()`, `hook_steps()` and `targets()` don't read the stamp.

## Refusal text

Print to stderr, then `return 1`:

```
refusing to update: adws/adw_sssf_config/stamp.json is malformed, and nothing was written
  record "adws/adw_modules/a\u0000b.py": key holds a null byte
  record "adws/adw_retired.py": "sha256" missing or not 64 lowercase hex characters
restore it from git, see who changed it, then run the update again:
  git checkout -- adws/adw_sssf_config/stamp.json
  git log -- adws/adw_sssf_config/stamp.json
  uv run .claude/skills/sssf/scripts/install.py --update
```

Print one indented line per problem, top-level problems first, then records in sorted key
order. Every bad record is listed, not only the first.

## Files to change

### `.claude/skills/sssf/scripts/install.py`

- Add `CONFIG`, `SHA256_HEX`, `STARTER_SOURCES` and `read_stamp()` as above. Give
  `read_stamp` a docstring that says why it reads once, and why roster sources are narrowed.
- In `update()`, replace `record = load_stamp(root)` with `record, problems =
  read_stamp(root)` and the refusal. Use `CONFIG` in place of the local `config` string, or
  keep the local and make it equal `CONFIG`. Change nothing else in `update()`.
- In the module docstring, after "--update removes SSSF code the skill dropped, if the repo
  never changed it.", add one sentence: `--update refuses, writing nothing, when stamp.json
  is malformed: restore it with git checkout and run it again.`

### `.claude/skills/sssf/tests/test_update.py`

Use the existing `repo` fixture, which installs the shipped `claude_code` roster from
`templates/`, and the autouse `private_registry` from `conftest.py`. Add:

- `run_update()`: like `install("--update")`, but it returns the `CompletedProcess` without
  asserting the exit code.
- `snapshot(root)`: maps every path under `root` (`os.walk`, `followlinks=False`, including
  `.git` if there) to `(st_mode, bytes or readlink or None)`. Also include the
  `SSSF_REGISTRY` file's bytes, or `None` when it is absent.
- `assert_refused(result, *keys)`: `returncode == 1`; `"Traceback" not in result.stderr`;
  `"git checkout -- adws/adw_sssf_config/stamp.json"` and
  `"git log -- adws/adw_sssf_config/stamp.json"` in stderr; `json.dumps(k)` in stderr for
  each key.
- A `primed` fixture on top of `repo`, so that an unchecked update *would* write. It calls
  `pretend_an_older_skill_stamped(REVIEWER)` (update would copy). It deletes one stamped
  prompt file (update would add). It writes `RETIRED` and calls `record_retired(RETIRED)`
  (update would delete). The bad record goes in after this.
- `test_a_malformed_record_is_refused_before_any_write`, parametrized over `(key, value)`.
  For each case: add the record to the stamp from `stamp()`, write it with `json.dumps`, take a
  snapshot, run the update, call `assert_refused(result, key)`, and check the snapshot is
  unchanged. Cases:
  - `"adws/adw_modules/a\x00b.py"`, a valid source and `sha256`
  - `"adws/adw_modules/\ud800.py"`, a valid source and `sha256`
  - `"adws/zz_gone.py"`, `{"source": "adws/zz_gone.py"}` (no `sha256`)
  - `"adws/zz_gone.py"`, `sha256` set to `"0" * 63`, then `"z" * 64`, then `"0" * 64 + "\n"`
  - `"adws/zz_gone.py"`, values of `"x"`, `[]`, `None` and `1`
  - `"adws/zz_gone.py"`, `{"sha256": "0" * 64}` (no `source`), and `{"source": 1, "sha256": "0" * 64}`
  - an **existing** record with its `sha256` removed: `str(AGENTS)`, which is the copy-loop
    crash on baseline
  - `str(CONFIG)` with `source` set to `"nope.yaml"`, `"adws"`, `"../../../etc/hostname"` and
    `"sssf.config.yaml\x00"`
  - `"adws/adw_sssf_config/sssf.config.x.yaml"`, source `"adws/adw_modules/agents.py"`
- `test_every_bad_record_is_named_at_once`: two bad records. Both keys are in stderr, and the
  snapshot is unchanged.
- `test_a_malformed_stamp_file_is_refused`, parametrized over the raw bytes or the setup for
  `stamp.json`. Each asserts `returncode == 1`, no traceback, the `git checkout` line, and an
  unchanged snapshot (on `primed`). Cases:
  - `b"{"`
  - `b"\xff\xfe"`
  - `b"[]"`
  - `b"{}"`
  - `b'{"files": []}'`
  - `b"[" * 100000`
  - `b'{"files": {}, "skill": "\\ud800"}'`
  - `b'{"files": {}, "skill": 1}'`
  - `stamp.json` replaced by a folder
  - `stamp.json` replaced by a symlink to a valid copy of itself outside the repo. Also
    assert that the target's bytes are unchanged.
- `test_a_stamp_from_every_install_shape_updates`, parametrized over `[]`,
  `["--coding-agent", "claude_code"]`, `["--chatgpt"]` and `["--container"]`. Install into
  a fresh `tmp_path` with those args. Then run `--add-roster` for each starter that isn't
  already the repo's roster, and `install("--update")` twice. Each run asserts exit 0 and no
  `"refusing"` in stderr. The second run also asserts `"updated: 0"`.
- `test_a_stamp_without_skill_and_with_an_extra_key_updates`: on `repo`, drop `skill`, add
  `"note": "x"`, re-dump with `indent=2, sort_keys=True`, and `install("--update")` succeeds.

The existing retire tests (`test_update_leaves_a_retired_link_to_a_folder`,
`test_update_never_deletes_through_a_traversal_key_or_a_linked_parent`) already use
well-formed records, `"0" * 64` and real hashes, so they still pass. Don't change them.

## Docs this change makes out of date

- `.claude/skills/sssf/references/config.md:485`: delete the known-limit bullet ("A
  `stamp.json` record whose path holds a null byte, or that has no `sha256`, makes
  `install.py --update` stop with a traceback…"). In its place, add a bullet that says what
  the update does now: `install.py --update` checks every `stamp.json` record before it
  writes anything. A record with a null byte or non-UTF-8 key, a missing or non-hex `sha256`,
  a missing `source`, or a roster record that names no starter roster, makes it refuse with
  nothing written and name the record. So does a `stamp.json` that isn't a regular file of
  JSON with a `files` object. Restore it with
  `git checkout -- adws/adw_sssf_config/stamp.json`, check `git log` on it, and run the update
  again. The forged-record bullet at `:484` stays as it is.
- `.claude/skills/sssf/cookbooks/install.md`: after the table under "`--update` uses it to
  decide" (around line 79), add one short paragraph. A malformed `stamp.json` makes `--update`
  refuse before it writes anything, naming each bad record. Restore the file with
  `git checkout -- adws/adw_sssf_config/stamp.json`, read `git log` on it, and run the update
  again.
- The `install.py` module docstring (above), which is also its `--help` text.
- No CHANGELOG: the repo doesn't keep one.
- `docs/designs/agentsh-local-sandbox.md`, Next item 4, and its "Left open" paragraph about the
  crash become out of date once this merges. Don't edit them in this build. The design doc
  records each guard run after it finishes, with its report, as it did for 8b800eb5.
- For the documenter: no `app_docs/` write-up is edited here. Note in this change's write-up
  that any earlier write-up of 8b800eb5 that lists the malformed-record crash as a known limit
  now describes old behaviour.

## Verification

Run the repo's own test command, the `test` entry in `adws/adw_modules/quality.py`:

```
uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider
```

It must exit 0. It covers the new tests in `.claude/skills/sssf/tests/test_update.py` and
every existing install and update test (`test_update.py`, `test_install_caches.py`,
`test_install_hook.py`, `test_registry.py`), so no extra check is needed.

By hand, on a scratch install (`cd $(mktemp -d) && git init -q && uv run
<worktree>/.claude/skills/sssf/scripts/install.py`):

1. Add a record `"adws/adw_modules/a\u0000b.py": {"source": "x", "sha256": "<64 zeros>"}`
   to `stamp.json`. `--update` exits 1, stderr names `"adws/adw_modules/a\u0000b.py"` and
   has no traceback, and `git status --porcelain` shows only the edited `stamp.json`.
2. `git checkout -- adws/adw_sssf_config/stamp.json`, then `--update` exits 0 and prints
   `up to date`.

Before handing in: grep `update()` for any `except`. There must be none new; the only new
`except` is in `read_stamp`.
