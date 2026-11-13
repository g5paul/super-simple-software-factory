"""scripts/check_prices.py compares prices.json with claude.com/pricing.

The page below is cut down from the real one (2026-11-02): each model's name,
then its labelled prices in separate elements, and Haiku's two prompt tiers.

Run from the skill root:
    uv run --with pytest --with pydantic --with pyyaml --with python-dotenv --with rich pytest tests
"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_prices", SKILL / "scripts" / "check_prices.py")
check_prices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_prices)


def card(name: str, read: str, write: str, input_: str, output: str) -> str:
    return (f'<h3 class="modelTitle">{name}</h3><p>A model</p><p>Prompt caching</p>'
            f'<p>Read</p><p>${read}</p><span>/ MTok</span><p>Write</p><p>${write}</p><span>/ MTok</span>'
            f'<p>Input</p><p>${input_}</p><span>/ MTok</span><p>Output</p><p>${output}</p><span>/ MTok</span>')


HAIKU = ('<h3>Haiku 5.5</h3><p>Fastest, most cost-efficient model</p><p>Prompt caching</p>'
         '<p>Prompts &le; 100K tokens</p><p>Read</p><p>$0.01</p><p>Write</p><p>$0.125</p>'
         '<p>Prompts &gt; 100K tokens</p><p>Read</p><p>$0.05</p><p>Write</p><p>$0.625</p>'
         '<p>Input</p><p>Prompts &le; 100K tokens</p><p>$0.10</p><p>Prompts &gt; 100K tokens</p><p>$0.50</p>'
         '<p>Output</p><p>Prompts &le; 100K tokens</p><p>$0.50</p><p>Prompts &gt; 100K tokens</p><p>$2.50</p>'
         '<p>Get faster speeds with fast mode for Opus 5.5 at 2x standard pricing.</p>')


def page(opus_output: str = "20") -> str:
    return ("<html><body>" + card("Fable 5.2", "0.20", "10", "8", "40")
            + card("Fable 5.1", "0.25", "12.50", "10", "50")
            + card("Opus 5.5", "0.20", "5", "4", opus_output) + card("Sonnet 5.5", "0.10", "2.50", "2", "10")
            + HAIKU + card("Opus 4.8", "0.50", "6.25", "5", "25") + "</body></html>")


def table(tmp_path: Path) -> Path:
    path = tmp_path / "prices.json"
    path.write_text(check_prices.TABLE.read_text())
    return path


def run(tmp_path: Path, html: str, *args: str) -> tuple[int, str, Path]:
    path = table(tmp_path)
    out = io.StringIO()
    code = check_prices.main([*args, "--table", str(path)], read=lambda: html, out=out)
    return code, out.getvalue(), path


def test_the_page_parses_into_each_models_prices_and_haikus_tiers():
    parsed = check_prices.parse(page())
    assert parsed["claude-opus-5-5"] == {"cache_read": 0.2, "cache_write_5m": 5, "input": 4, "output": 20}
    assert parsed["claude-haiku-5-5"]["input"] == 0.1
    assert parsed["claude-haiku-5-5"]["long_prompt"] == {
        "over": 100_000, "cache_read": 0.05, "cache_write_5m": 0.625, "input": 0.5, "output": 2.5}


def test_the_shipped_table_matches_the_page(tmp_path):
    code, said, _ = run(tmp_path, page())
    assert code == 0 and "matches" in said
    assert "not checked, priced from another page: claude-sonnet-5" in said


def test_a_model_priced_elsewhere_is_left_alone_by_write(tmp_path):
    code, said, path = run(tmp_path, page(opus_output="25"), "--write")
    assert code == 0
    written = json.loads(path.read_text())
    assert written["models"]["claude-sonnet-5"]["output"] == 10
    assert "claude-sonnet-5" in written["elsewhere"]


def test_a_changed_price_is_named_and_left_alone_without_write(tmp_path):
    code, said, path = run(tmp_path, page(opus_output="25"))
    assert code == 1
    assert "claude-opus-5-5: output is 20 here, 25.0 on the page" in said
    assert json.loads(path.read_text())["models"]["claude-opus-5-5"]["output"] == 20


def test_write_applies_the_page_and_keeps_the_one_hour_rule(tmp_path):
    code, said, path = run(tmp_path, page(opus_output="25").replace("<p>$4</p>", "<p>$4.50</p>"), "--write")
    assert code == 0 and "wrote" in said
    written = json.loads(path.read_text())
    opus = written["models"]["claude-opus-5-5"]
    assert (opus["output"], opus["input"], opus["cache_write_1h"]) == (25, 4.5, 9)
    assert "read 20" in written["source"]
    assert written["models"]["claude-haiku-5-5"]["long_prompt"]["over"] == 100_000


def test_a_page_without_the_tables_models_says_the_layout_changed(tmp_path):
    code, said, _ = run(tmp_path, "<html><p>Pricing</p></html>")
    assert code == 2 and "layout" in said
