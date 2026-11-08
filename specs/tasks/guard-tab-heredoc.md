# Make the agent rules see a split command in a `<<-` heredoc

Guard run 6e1c3a88 taught the three checkers to search the command with every backslash-newline removed (37273db). Its review left one broken promise. A `<<-` heredoc strips each body line's leading tabs before the inner shell reads it, so

```
bash <<-'EOF'
	ju\
	st test live
	EOF
```

runs `just test live`. The joined text keeps the tab, `ju<TAB>st test live`, and matches nothing. The review proved it with a harmless `ec\` and `ho word`, and found that `sh <<-'EOF'`, `cat <<-'EOF' | bash`, and the PI, PUSH and secret-file rules pass the same way. The old code missed it too. The guard report is `adws/adw_data/sessions/6e1c3a88/context_handoff/guard_report.md`.

## What should change

- **A third search text in each checker:** the command with every backslash-newline and the tabs right after it removed. A match on any of the three texts counts. The joined text from 37273db stays, because the shell reads a tab after a continuation as a word break outside a `<<-` body: `just test\` newline tab `live` runs `just test live`, which the third text would read as `just testlive`.
- **The same in all three checkers**, `rules.breach`, `sssf_refuse.refusal` and `sssf_rules.ts`, as 37273db did for the joined text.
- **Docs.** `config.md`'s rule section names the third text.

## The bar

The bar of `specs/tasks/guard-backslash-newline.md` stands, with the same accepted limits. A split command inside a `<<-` body indented with tabs must be stopped or refused as the unsplit command would be, on both harnesses, through each checker. Every command that matches today must still match.

## Tests

- Each checker matches the example above, `sh <<-'EOF'`, and `cat <<-'EOF' | bash`, with the starter rules and `SSSF-REFUSE-PROBE`.
- `just test\` newline tab `live`, outside a heredoc, still matches.
- The Python and JavaScript checkers agree on the shared table.
- Every existing test passes unchanged.

## Out of scope

- Spaces after a continuation in a `<<-` body. The shell strips only tabs there.
- Everything else out of scope in the backslash-newline request.
