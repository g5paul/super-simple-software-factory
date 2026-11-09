# Refuse a malformed stamp record before the update writes anything

Item 4 of the Next list in `docs/designs/agentsh-local-sandbox.md`. Run 8b800eb5 found it, and its report is `docs/designs/agentsh/guard-run-8b800eb5.md`. Start from `main`.

## The problem

`install.py --update` reads the project's `adws/adw_sssf_config/stamp.json`, which an agent can edit. Since 8b800eb5 it also removes SSSF files a newer skill retired, going by the records in that file. A malformed record makes the update stop with a traceback partway through:

- a record whose path holds a null byte: `retire()` catches only `OSError`, and `os.open` raises `ValueError`;
- a record with no `sha256`: `update()` reads `files[rel]["sha256"]` unchecked, and raises `KeyError`.

The crash comes after the template copies and before `save_stamp`, `ensure_gitignore`, the register step and the summary. The update has then copied some files and not recorded them, and retired files sorted after the bad record stay. Nothing is deleted or overwritten wrongly. `config.md` lists this as a known limit and tells the engineer to restore `stamp.json` with `git checkout`.

The copy loop reads records too: `base["sha256"]` and `entry["source"]`. A record that isn't an object, or that lacks `source` or `sha256`, crashes there on `main` as well. So does a `stamp.json` that isn't valid JSON, or whose `files` isn't an object, though that crash comes before any write.

## What should change

1. **`--update` checks every record before it writes anything.** A record is well formed when its key is a string with no null byte and its value is an object whose `source` and `sha256` are strings, `sha256` being 64 hex characters. Also check that `stamp.json` is a JSON object whose `files` is an object. The spec names every place `update()` and its helpers read the stamp, and shows each one sits behind the check.
2. **A bad record is a refusal, not a traceback.** The update exits non-zero with nothing written, names each bad record with its key escaped, and says to restore `stamp.json` with `git checkout -- adws/adw_sssf_config/stamp.json`, check `git log` on it, and run the update again.
3. **A well-formed stamp updates as before.** The refusal never fires on a stamp that an install or update wrote, on any skill version since `stamp.json` began.
4. **`config.md`** drops the known limit on the crash and says what the update does now.

## The bar

The earlier bars stand. A stamp record that makes `--update` write, copy, delete or record anything before it refuses, or crash with a traceback, is a broken promise. A refusal of a stamp written by an install or update is a false positive. A list of exceptions to catch, a repair mode, or a flag to skip the check is a finding. The check is a narrowing: a record either passes it or the update refuses.

## This run is the first under agentsh

The hub's agents now run under the agentsh service, with the policy `sssf-super-simple-software-factory-claude`. If agentsh denies something an agent needs for this task, report the denied action and what it was for under its own heading in your report. Don't look for another way to do it.

## Out of scope

A forged but well-formed record, which can still have `--update` delete an in-repo SSSF file (a known limit). The version check in `agentsh_install.py`. A FIFO or a huge file at a stamped path. The policy rules, pi under the wrap, sudo.
