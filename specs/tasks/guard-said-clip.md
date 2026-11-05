# Keep git's reason when the breach clips a long error line

`_said` in `permissions.py` quotes git's error in a breach: at most 20 lines,
each cut to its first 300 characters, with non-printables shown as `?`. Git
often puts the path first and the reason last, as in
`fatal: unable to access '<path>': Too many levels of symbolic links`. When
the path is long, the cut drops the reason, so the engineer sees a breach that
doesn't say what's wrong. The run still stops, so nothing gets past the guard.

The reproduction: run the hub's guard tests with TMPDIR set to a path of about
290 characters. Three tests fail on `main`, each because the reason fell off
the end:
`test_a_symlink_loop_in_an_include_stops_the_run_first`,
`test_an_unsearchable_dir_before_dotdot_stops_the_run_first` and
`test_a_broken_global_config_outside_a_repo_stops_the_run`.

What should change: a line longer than the limit keeps its start and its end,
with a marker between them showing that the middle was cut, so the reason git
gives at the end survives. The line limit and the 20-line limit stay as they
are, and so does the `?` for non-printables. A line within the limit is quoted
exactly as today. The three tests pass under a long TMPDIR.
