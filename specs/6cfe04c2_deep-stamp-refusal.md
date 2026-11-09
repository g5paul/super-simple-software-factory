# Finish the malformed-stamp refusal: deep nesting, and the extra tries

This follows run 056faf3e (task `specs/tasks/guard-install-update-stamp.md`, spec
`specs/056faf3e_malformed-stamp-refusal_v2.md`), which passed in round 1. This branch holds
that run's code. The engineer wants its first broken promise fixed before merging, and the
whole change is reviewed against `main`.

The guard code is `.claude/skills/sssf/scripts/install.py`, not a file under
`templates/adws/adw_modules/`. `--update` lives in the installer, and no module under
`adw_modules/` reads `stamp.json` on the update path.

## Threat model

- An agent runs as the engineer's own user, in the project's checkout. It can write
  `adws/adw_sssf_config/stamp.json` with any bytes, between runs or during one. The engineer
  later runs `uv run .claude/skills/sssf/scripts/install.py --update` as themselves.
- `--update` treats the stamp as data. The agent's goal is to turn that data into a write,
  copy, delete or record the engineer didn't intend. Failing that, it wants a traceback partway
  through, which leaves the repo half-updated: templates copied but unrecorded, retired files
  deleted but still recorded, no registry entry.
- The new hole on head: `json.loads` uses the C parser, which reads 1,200 or more levels of
  brackets. `save_stamp` then calls `json.dumps(..., indent=2)`, which uses the pure-Python
  encoder, and that is bound by the recursion limit. So a deeply nested value anywhere in the
  stamp passes `read_stamp`. `--update` then copies, deletes a retired file, and crashes with
  `RecursionError` in `save_stamp`. Measured on this branch with Python 3.12: nesting at the
  top level (`"note": [[[…]]]`), or in an extra key of a record that `update()` keeps verbatim
  (such as an unshipped, non-SSSF-code record like `adws/zz_gone.py`), exits 1 with a
  `RecursionError` at 1,200, 2,000 and 5,000 levels. It works at 900 and 990.
  (An extra key on a record that `update()` rewrites, such as an unchanged `agents.py`, is
  dropped before the save, so it doesn't crash.)
- What this guard can't stop at its level: the agent can edit or delete any SSSF file in the
  repo directly, because it runs as the same user. A forged but well-formed record can still
  steer `--update` (see Out of scope). The installer can't tell an honest record from a forged
  one with the same shape. This change narrows only the shape of what `--update` acts on.
- The stamp is read once. The check and every later use work from the same parsed `dict`, so
  rewriting the file mid-update can't make them disagree.

## Baseline

`main` at `6c1421d` ("Tell the bypass reviewer how to make links under agentsh"). Every finding
is judged against that commit. On it, `update()` calls `load_stamp()` and reads the result
unchecked. A deeply nested stamp crashes there in `save_stamp`, after the copies and the
retires, just as on head. Run 056faf3e's `read_stamp` refusal is on this branch but not on
`main`. This change completes it.

## Bar

The bar in `specs/tasks/guard-install-update-stamp.md` stands. The change must:

1. Refuse a stamp nested too deeply to save, at the top level or inside any record, before any
   write, copy, delete or record, and without a traceback. **Every stamp `read_stamp` accepts
   must save.**
2. Keep `read_stamp` to the **one** `try`, the one around the single read and parse. The
   surrogate checks on `skill` and on record keys use `isinstance` and a scan, with no `try`.
3. Update every stamp an install, `--add-roster` or `--update` wrote, exactly as before. Those
   stamps are at most four levels deep, so no false positive is possible.

The change must never:

- Write, copy, delete or record anything, or register the repo, before it refuses.
- Crash with a traceback on any `stamp.json` contents.
- Raise the recursion limit (`sys.setrecursionlimit`), add an `except` anywhere else, catch a
  new exception type, or parse the file a second time. Encoding the parsed record is not a
  parse: it is what `save_stamp` will do with it.
- Keep an agent file, run agent code, or delete or overwrite an engineer file where the
  baseline doesn't.

## Out of scope

Leave these open, and file them as old holes, not findings:

- Forged but well-formed records. Beyond listing them in `config.md`, they stay:
  - a record whose `sha256` matches a real `adws/adw_*.py` or `adws/adw_modules/` file
    makes `--update` delete it (already listed);
  - a record whose `sha256` matches the engineer's customized stamped file, such as
    `quality.py`, makes `--update` overwrite it with the template;
  - a forged key under `container/` makes `--update` add the container templates the
    engineer never asked for.
- Everything under Out of scope in `specs/056faf3e_malformed-stamp-refusal_v2.md`: a linked
  `stamp.json` or `adws/adw_sssf_config/`, a rewrite after the single read, a folder at a
  stamped target path, `install` and `--add-roster` on a malformed stamp.
- The version check in `agentsh_install.py`.
- A FIFO or a huge file at a stamped path, or at `stamp.json`.
- The policy rules, pi under the wrap, sudo.

## Changes

### `.claude/skills/sssf/scripts/install.py`

1. **One encoder for the stamp.** Add, next to `save_stamp`:

   ```python
   def stamp_text(record: dict) -> str:
       return json.dumps(record, indent=2, sort_keys=True) + "\n"
   ```

   Make `save_stamp` write `stamp_text(record)`, with no other change. Its callers (`install`,
   `add_roster`, `update`) behave as before. `read_stamp` sits above `save_stamp` in the file,
   which is fine for a module-level function.

2. **Encode inside the existing `try` in `read_stamp`.** Replace the try block with:

   ```python
   try:
       record = json.loads((root / STAMP).read_text(encoding="utf-8"))
       stamp_text(record)   # what save_stamp will write: a stamp that can't save is malformed
   except FileNotFoundError:
       return {"files": {}}, []
   except OSError:
       return {}, ["stamp.json can't be read"]
   except ValueError:
       return {}, ["stamp.json is not valid UTF-8 JSON"]
   except RecursionError:
       return {}, ["stamp.json is nested too deeply"]
   ```

   This keeps the same four exception types as head. It splits `(ValueError, RecursionError)`
   into two clauses only so the message is accurate. No new type, no second `try`.
   Why this covers every save: `update()` calls `read_stamp` and `save_stamp` directly, so
   `stamp_text` runs at the same stack depth both times. Between them `update()` only replaces
   records under `files` with flat `{"source", "sha256"}` dicts, deletes records, and sets
   `skill` to a string, so it never makes the record deeper. A record that encodes in
   `read_stamp` encodes in `save_stamp`. Don't add a nesting-depth counter or a margin.
   Encoding raises nothing else on a `json.loads` result: keys are `str`, `NaN` is allowed,
   and `ensure_ascii` escapes lone surrogates.

3. **Drop the two extra tries.** Add a module constant next to `SHA256_HEX`:

   ```python
   SURROGATE = re.compile("[\ud800-\udfff]")   # the only str a UTF-8 encode rejects
   ```

   Write it with the `\ud800`/`\udfff` escapes in the source, not literal characters.
   Then:
   - `skill`: `if "skill" in record and (not isinstance(record["skill"], str) or SURROGATE.search(record["skill"])): problems.append('"skill" is not text')`.
     Delete the `try`/`raise UnicodeEncodeError`/`except` block.
   - record keys: inside the existing `else` branch for `isinstance(rel, str)`, keep the null
     byte check and replace the `try: rel.encode("utf-8") … except` with
     `if SURROGATE.search(rel): reasons.append("key is not UTF-8 text")`.
   - The messages are unchanged.
   After this, `read_stamp` has exactly one `try`.

4. **Docstring of `read_stamp`.** Add one sentence: the record is encoded the way `save_stamp`
   will encode it, inside the same `try`, so a stamp nested too deeply to save is refused
   before anything is written. Everything that `read_stamp` accepts saves.

Change nothing in `update()`.

### `.claude/skills/sssf/tests/test_update.py`

Use the existing `repo` and `primed` fixtures, `run_update`, `snapshot`, `assert_refused`,
`GONE` and `GOOD`. Every test builds on the stamp a real install of the shipped `claude_code`
roster wrote.

- Add a helper `write_nested(where: str, depth: int) -> None`. It loads `stamp()`. For
  `"top"` it sets `record["note"] = "__DEEP__"`. For `"record"` it sets
  `record["files"][GONE] = {"source": GONE, "sha256": GOOD, "extra": "__DEEP__"}`, a record
  `update()` keeps verbatim, since `GONE` isn't shipped and isn't SSSF code. It writes
  `json.dumps(record).replace('"__DEEP__"', "[" * depth + "]" * depth)` to `STAMP`. **Never
  build the deep value as a Python list and `json.dumps` it in the test**, because that
  recurses in the test process.
- `test_a_stamp_too_deep_to_save_is_refused_before_any_write`, parametrized over `where` in
  `["top", "record"]` and `depth` in `[1200, 2000, 5000]`. On `primed`: `write_nested`, take
  a `snapshot`, `run_update()`, `assert_refused(result)`, check that
  `"nested too deeply" in result.stderr` and that the snapshot is unchanged. Without the fix,
  `primed` makes the update copy `REVIEWER`, add the deleted prompt and delete `RETIRED`
  before it crashes, so this test fails on head.
- `test_a_nested_stamp_that_saves_still_updates`, parametrized over `where` in
  `["top", "record"]` and `depth` in `[900, 990]`. On `repo`: `write_nested`, then
  `run_update()` twice. Each run has `returncode == 0`, no `"refusing"` and no `"Traceback"`
  in stderr. After the first run, `STAMP.read_text()` still contains `"[" * depth`, which
  shows that the nested value was saved, not dropped.
- `test_whatever_read_stamp_accepts_saves`: the invariant, at the boundary. Load the module the
  way `test_retire_unlinks_through_the_open_parent_not_a_re_resolved_path` does
  (`importlib.util.spec_from_file_location`). On `repo`, binary-search `depth` in
  `[1, 20000]` with `write_nested("top", depth)` for the largest depth `n` at which
  `mod.read_stamp(Path.cwd())` returns no problems. Call `read_stamp` and `save_stamp` from the
  same function, so they run at the same stack depth, as they do in `update()`. Then assert:
  - at `n`, `mod.save_stamp(Path.cwd(), record)` raises nothing;
  - at `n + 1`, `read_stamp` returns `["stamp.json is nested too deeply"]`.
  Pin `n` loosely as well (`100 < n < 20000`), so the search can't pass trivially.
- `test_read_stamp_has_one_try`: parse the source of `install.py` with `ast`, find
  `read_stamp`, and assert it holds exactly one `ast.Try` node and that no `ast.Call` in it
  names `setrecursionlimit`. This pins the bar's one-try rule.

The existing `test_a_malformed_stamp_file_is_refused` cases (`b"[" * 100000`, `"\ud800"`
skill, `1` skill) and the surrogate-key case in `MALFORMED_RECORDS` cover item 3 and must
still pass unchanged.

## Docs this change makes out of date

- `.claude/skills/sssf/references/config.md`, the known-limits list (around lines 482–486):
  - In the bullet that starts "`install.py --update` checks every `stamp.json` record",
    change "So does a `stamp.json` that can't be read, isn't JSON, or has no `files` object."
    to "So does a `stamp.json` that can't be read, isn't JSON, is nested too deeply to save, or
    has no `files` object."
  - Extend the forged-record bullet ("`install.py --update` removes SSSF files a newer skill
    retired…") with two more, in the same voice. Keep them as one bullet or as adjacent
    bullets right after it:
    - A forged record whose `sha256` matches a file the engineer customized, such as
      `quality.py`, makes `--update` overwrite that file with the template, as if it were
      untouched.
    - A forged key under `container/` makes `--update` add the container templates the repo
      never asked for. A file already there that differs goes to review (`!`), and isn't
      overwritten.
    End with the existing advice: run `git status` after an update.
- The `install.py` module docstring (its `--help`) already says a malformed stamp is refused,
  so it needs no change. `.claude/skills/sssf/cookbooks/install.md` says the same in general
  terms, so it needs no change either.
- No CHANGELOG: the repo doesn't keep one.
- `docs/designs/agentsh-local-sandbox.md`, Next item 4: don't edit it in this build. The
  design doc records guard runs after they finish.
- For the documenter: don't edit anything in `app_docs/`. If run 056faf3e's write-up says any
  stamp `read_stamp` accepts is safe to save, or lists two `try`s for surrogates, note in this
  change's write-up that it describes the old behaviour.

## Verification

Run the repo's own `test` command, from `adws/adw_modules/quality.py`:

```
uv run --directory .claude/skills/sssf --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests -q -p no:cacheprovider
```

It must exit 0. It runs the new tests in `.claude/skills/sssf/tests/test_update.py` and
every existing install and update test. No extra check is needed.

By hand, on a scratch install (`d=$(mktemp -d) && cd "$d" && git init -q && uv run
<worktree>/.claude/skills/sssf/scripts/install.py`):

1. Put `"note": [[[…]]]` at 1,200 levels into `stamp.json`, by text substitution. Then
   `--update` exits 1, stderr says "nested too deeply" and has no traceback, and
   `git status --porcelain` lists nothing new beyond the stamp.
2. Repeat at 990 levels: `--update` exits 0, then exits 0 again.

Before handing in:
- `read_stamp` holds exactly one `try`, and `install.py` has no `setrecursionlimit`.
- `update()` is unchanged from head.
- `save_stamp` and `read_stamp` both encode through `stamp_text`.
