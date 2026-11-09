# Finish the malformed-stamp refusal: deep nesting, and the extra tries

This follows run 056faf3e, from `specs/tasks/guard-install-update-stamp.md`, which passed in round 1. This branch holds its code, carried from `sssf/056faf3e`, and its spec is `specs/056faf3e_malformed-stamp-refusal_v2.md`. The engineer chose to fix its first broken promise before merging. Review the whole change against `main`.

## What should change

1. **A deeply nested `stamp.json` is refused before any write.** `json.loads` reads 1,200 levels of brackets, because the C parser isn't bound by Python's recursion limit. `save_stamp` then calls `json.dumps(indent=2)`, the pure-Python encoder, which is. So `--update` copies files, deletes a retired file, and crashes with `RecursionError` before the stamp is saved. This holds for nesting at the top level and inside an extra key of a record, at 1,200, 2,000 and 5,000 levels. 900 and 990 levels work. The reviewer's fix: inside the one existing try in `read_stamp`, encode the record the way `save_stamp` will, and treat a `RecursionError` as malformed. Any stamp that `read_stamp` accepts must save.
2. **`read_stamp` keeps to the one try the spec allows.** Head adds two more, around `skill.encode` and `rel.encode`, to catch surrogates. Check the types with `isinstance` and scan the strings for surrogates with no try.
3. **`config.md` lists two more forged-record holes** with the existing one. Both are old, and both come from a forged but well-formed record:
   - a record whose `sha256` matches the engineer's customized stamped file, `quality.py` say, makes `--update` overwrite it with the template;
   - a forged key under `container/` makes `--update` add the container templates the engineer never asked for. A differing file there goes to review, not overwrite.

## The bar

The bar in `specs/tasks/guard-install-update-stamp.md` stands: a stamp that makes `--update` write, copy, delete or record anything before it refuses, or crash with a traceback, is a broken promise. A refusal of a stamp an install or update wrote is a false positive. A raised recursion limit, a list of exceptions, or a second parse is a finding.

## Out of scope

The forged-record holes themselves, beyond listing them. The version check in `agentsh_install.py`. A FIFO or a huge file at a stamped path. The policy rules, pi under the wrap, sudo.
