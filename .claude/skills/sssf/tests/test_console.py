"""The console narrates; the trace is the record. Losing the first must not cost the second."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent


def test_a_closed_stdout_does_not_end_the_run(tmp_path):
    """Run 84f61760 died mid-verify because its operator piped it through
    `head -40`. The console must go quiet, not exit, when stdout closes."""
    script = tmp_path / "pipe.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(SKILL / 'templates' / 'adws')!r})\n"
        "from adw_modules.console import Console\n"
        "class T:\n"
        "    def event(self, record): pass\n"
        "c = Console(T(), 'x')\n"
        "for i in range(2000): c.note(f'line {i}')\n"
        "open(sys.argv[1], 'w').write('finished')\n")
    marker = tmp_path / "marker"
    done = subprocess.run(f"{sys.executable} {script} {marker} | head -3", shell=True,
                          capture_output=True, text=True)
    assert done.stdout.count("\n") == 3
    assert marker.read_text() == "finished", done.stderr
