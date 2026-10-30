"""Each review round keeps its own report.

The reviewer prompt used to write every round to review.md, so round 2
overwrote round 1 and the reasons for the first rejection were lost. The
prompt now gets the phase name (review_1, review_2, ...) and names the
file after it.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL / "templates" / "adws"))

from adw_modules import agents, prompts
from adw_modules.data_types import AgentCall, GenericOutput, SSSFConfig

REVIEWER = SKILL / "templates" / "prompt_engineering" / "reviewer" / "user.md"


def variables(phase_name: str) -> dict[str, str]:
    run = SimpleNamespace(context_handoff_dir=Path("/handoff"), cfg=SSSFConfig())
    phase = SimpleNamespace(params=SimpleNamespace(name=phase_name))
    return agents.prompt_variables(run, phase, AgentCall(output_type=GenericOutput, prompt="p"))


def test_prompts_get_the_phase_name():
    assert variables("review_2")["phase_name"] == "review_2"


def test_reviewer_writes_one_file_per_round():
    template = REVIEWER.read_text()
    assert "review.md" not in template
    assert "<context_handoff_dir>/<phase_name>.md" in template
    rendered = prompts.render(REVIEWER, variables("review_2"))
    assert "### phase_name\n\nreview_2" in rendered
