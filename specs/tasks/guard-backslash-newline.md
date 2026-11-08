# Make the agent rules see through backslash-newline

Guard run c285e8ad's review found that a backslash-newline gets past every `forbid` rule, on both harnesses and in every mode. The shell drops a backslash followed by a newline before it reads the command, so

```
ju\
st test live
```

runs `just test live`. But the three checkers search the text as written, so none of them sees `just`: `rules.breach`, which the stream check calls, `sssf_refuse.refusal`, Claude Code's prefix, and pi's `tool_call` handler in `sssf_rules.ts`. `docs/open.md` lists it as a build item.

The common case isn't a split word. Agents often write a long command over several lines with a trailing backslash, as in `just test \` then a newline then `live`, and `just\s+test\s+live` misses that too, since the backslash isn't whitespace. So this isn't only a way around the rules; it's a casual miss of exactly the kind `references/config.md` says a rule should catch.

## What should change

- **All three checkers also search the command with every backslash-newline removed.** A match on either the text as written or the joined text counts, so every pattern that matches today still matches, and patterns anchored with `^` or `$` keep matching line by line in both. Removing every backslash-newline, even one the shell would keep, such as inside single quotes or after an escaped backslash, can only add matches. That's the safe direction for a rule.
- **The same rule everywhere.** The stream check and both refusers must agree, as `config.md` promises ("search the same text, line by line"). That covers `sssf_refuse`'s searches of each `eval '...'` argument after it unquotes it, and pi's handler. A refuse rule's refusal and the stream check's breach must still come from the same match.
- **The ripgrep line in the OpenShell image's check.** `containerfile`'s check runs `rg --version`, which prints six lines into the build log. Use `rg --version | head -1` or an equivalent that still fails the build when `rg` is missing. A project's session reported it.
- **Docs.** `config.md`'s rule section says the search also reads the command with backslash-newlines removed, and keeps quotes inside a word as the known limit. Delete the `open.md` row for backslash-newline.

## The bar

A command that runs a forbidden command through a backslash-newline, anywhere in it, must be stopped or refused as the same command without it would be, on both harnesses, in every mode, through each checker that handles it today. Every command that matches today must still match. Anything else is a bypass.

Accepted, as today: quotes inside a word, and a backslash before an ordinary character inside a word, such as `j\ust test live`. Both are the same class, a deliberate split of the command word, and `config.md:429` already says a rule doesn't stop an agent working around it. Name `j\ust` in that known limit.

Accepted too, by the engineer on 2026-11-08 after guard run 70628873's spec review: **a line the shell doesn't continue, followed by a split command.** Joining every backslash-newline also joins one the shell keeps, so the line before swallows the command's start and a `^` or `\b` anchor misses it. Examples: `x\\` then a newline then `ju\` then a newline then `st test live`, and a comment line ending in a backslash before a split command. Closing it fully means searching from each line after a continuation, which is quadratic. Name it in the spec's known limits and in `config.md`'s, with pinned no-match tests, as the quotes limit is. Every other command split by a backslash-newline must match.

Run 70628873's draft and review, in `adws/adw_data/sessions/70628873/drafts/` and `../context_handoff/spec_review_1.md`, hold the cases the reviewer ran under real bash and a smaller `sssf_refuse` design, `texts = [*commands(line), *commands(CONTINUATION.sub("", line))]`, which met the bar on every case it tried. Start from them.

## Tests

- Each checker matches `ju\` newline `st test live`, `just test \` newline `live`, and the same inside `bash -c '...'`, inside a heredoc piped to `bash`, and inside Claude Code's `eval '...'` wrapper.
- A pattern anchored with `^` still matches at the start of each line, in the text as written and in the joined text.
- A refuse rule refuses the split command on both harnesses, and the stream check's breach agrees with the refusal.
- The Python and JavaScript checkers agree on a shared table of cases, as the existing parity tests do.
- The image's check line still fails when `rg` is missing.
- Every existing test passes unchanged. `test_pi_always_loads_the_refusing_extension_and_passes_rules_only_when_set` fails today when `SSSF_REFUSE_RULES` is set in the shell, as it is in an agent's, because its stand-in pi echoes the inherited value. Make that test clear the variable with `monkeypatch.delenv`. `agent_pi`'s behaviour stays: inheriting more rules only adds refusals.

## Out of scope

- Quotes inside a word and other shell unquoting.
- A line the shell doesn't continue, followed by a split command, as accepted above.
- The Python and JavaScript regex differences `config.md` lists. They apply to the joined text as to the written text.
- The model-host rules and the rest of the network policy.
- Building an image or running a sandbox.
