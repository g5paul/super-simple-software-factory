# Guard change report

Outcome: round 1 found nothing that blocks

Heads reviewed: `8db32d1e26d2`

## Bypasses (0)


## Harms (0)


## False positives (0)


## Broken promises (0)


## Old holes (3)

- `'{"files": {...honest...}, "note": [' + ','.join(['[' * 9000 + ']' * 9000] * 20) + ']}'` passes install.py --update (read_stamp/save_stamp). Base: passes. Head: passes.  Breaks 'never crash with a traceback on any stamp.json contents'. indent=2 writes about 2*n^2 spaces per n-deep value. A 374 KB stamp (3.13) or a 3.9 MB stamp (3.12, 2000 x 980 deep) raises MemoryError under a 2.5 GB cap: on base in save_stamp after the copy and the retire, on head in read_stamp before any write. A 20 KB stamp nested 9,990 deep on 3.13 makes --update write a 199,655,494-byte stamp.json on both. Out of scope covers only a huge file at stamp.json, not a small one that grows. New to the known limits.
- `'{"files": {...honest..., "notes/\\u2603": {"source": "gone2.txt", "sha256": "' + '0' * 64 + '"}}}'  # with PYTHONIOENCODING=ascii` passes install.py --update (summary print). Base: passes. Head: passes.  Breaks 'never crash with a traceback on any stamp.json contents'. On a non-UTF-8 stdout, a non-ASCII gone key or recorded skill raises UnicodeEncodeError in the summary print, after the stamp is saved, so the removed list and hook steps are never shown. Environment-dependent. New to the known limits.
- `'{"files": {...honest..., "notes/\\u001b[2K\\u001b[1A\\u001b[2K\\u001b[1A\\u001b[2K": {"source": "gone2.txt", "sha256": "' + '0' * 64 + '"}}}'` passes install.py --update (summary print). Base: passes. Head: passes.  The success summary prints gone record keys raw, so a forged key's terminal escapes can erase earlier lines, such as the removed: list. The refusal path escapes keys with json.dumps; the summary doesn't. Part of the out-of-scope forged-record class, but the display side isn't listed. New to the known limits.

220 candidates, 357 checks.
