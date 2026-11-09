# Guard change report

Outcome: round 1 found nothing that blocks

Heads reviewed: `f0d76877861b`

## Bypasses (0)


## Harms (0)


## False positives (0)


## Broken promises (2)

- `'{"files": {<the install\'s own records>}, "note": ' + '[' * 1200 + ']' * 1200 + '}'` passes install.py --update (read_stamp, then save_stamp). Base: copies 2 files, deletes the retired file, then RecursionError traceback; stamp not saved. Head: passes read_stamp, copies 2 files, deletes the retired file, then RecursionError traceback in save_stamp (install.py:579); stamp not saved, repo not registered.  Breaks 'never crash with a traceback on any stamp.json contents' and 'never write before it refuses', and disproves 'the record came from json.loads, so it dumps'. json.loads in C isn't bound by the Python recursion limit, but json.dumps(indent=2) runs the pure-Python encoder, which is. Also fails at 2000 and 5000 levels, and at 1200 levels inside an extra key of a record. 900 and 990 levels work. Narrowing fix: inside the existing try, round-trip json.dumps(record, indent=2, sort_keys=True) in read_stamp and treat RecursionError as malformed.
- `'{"files": {}, "skill": 1}'` passes read_stamp source (try count). Base: n/a. Head: refused correctly, but through an extra try that raises a made-up UnicodeEncodeError.  The spec says the only new try is the one around the single read and parse. Head adds two more in read_stamp: one around skill.encode and one around rel.encode. They're harmless and narrow, so this only breaks the spec's wording. An isinstance check plus a surrogate scan would meet it without a try.

## Old holes (2)

- `quality.py customized by the engineer; stamp record files['adws/adw_modules/quality.py']['sha256'] = sha256(customized quality.py)` passes install.py --update. Base: passes (overwrites the customized quality.py with the template). Head: passes (same).  This is the forged-record class the spec puts out of scope. config.md:484 lists only deletion by a forged record, not overwriting a customized stamped file. New to the known limits.
- `stamp record files['container/forged'] = {'source': 'x', 'sha256': '0'*64}` passes install.py --update. Base: passes (stamps container/ templates the engineer never asked for). Head: passes (same).  has_container trusts any key under container/, so a forged key makes the update add container files. Existing differing files go to review, not overwrite. This is a forged-record case, and config.md doesn't list it. New to the known limits.

66 candidates, 130 checks.
