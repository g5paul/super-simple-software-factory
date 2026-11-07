#!/usr/bin/env python3
"""Compare adws/adw_modules/prices.json with claude.com/pricing.

prices.json prices a Claude call SSSF killed mid-turn, and the dashboard's
account panel prices transcripts with it. Nothing else notices when Anthropic
changes a price, so run this now and then from the skill's source:

    uv run .claude/skills/sssf/scripts/check_prices.py          # report
    uv run .claude/skills/sssf/scripts/check_prices.py --write  # apply the page's prices

It reads the page's text, not an API: each model's name is followed by its
Read, Write, Input and Output prices, and Haiku's by a short and a long prompt
tier. The page lists 5-minute cache writes only, so a 1-hour write is checked
against SSSF's rule, twice input. Exit 0 when the table matches, 1 when it
differs, 2 when the page can't be read or holds none of the table's models.
After --write, commit the table and update the repos.
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import re
import sys
import urllib.request
from pathlib import Path

URL = "https://claude.com/pricing"
TABLE = Path(__file__).resolve().parent.parent / "templates" / "adws" / "adw_modules" / "prices.json"
LABELS = {"Read": "cache_read", "Write": "cache_write_5m", "Input": "input", "Output": "output"}
MODEL = re.compile(r"(Fable|Opus|Sonnet|Haiku) \d+(\.\d+)?")
PRICE = re.compile(r"\$(\d+(?:\.\d+)?)")
LONG = re.compile(r"Prompts > (\d+)K tokens")


def model_id(name: str) -> str:
    """`Opus 5.5` -> `claude-opus-5-5`."""
    return "claude-" + name.lower().replace(" ", "-").replace(".", "-")


def parse(page: str) -> dict[str, dict]:
    """Each model on the page, by id, with its prices per million tokens. A
    model with a long prompt tier gets `long_prompt`, with its threshold in `over`."""
    text = html.unescape(re.sub(r"<[^>]+>", "\n", page))
    parts = [part.strip() for part in text.splitlines() if part.strip()]
    models: dict[str, dict] = {}
    current, label, tier = None, None, None
    for part in parts:
        if MODEL.fullmatch(part):
            current, label, tier = models.setdefault(model_id(part), {}), None, None
        elif current is None:
            continue
        elif part in LABELS:
            label = LABELS[part]        # a tier, once named, holds until the next model
        elif part.startswith("Prompts ≤"):
            tier = "short"
        elif LONG.match(part):
            tier = "long"
            current.setdefault("long_prompt", {})["over"] = int(LONG.match(part).group(1)) * 1000
        elif label and PRICE.fullmatch(part):
            price = float(PRICE.fullmatch(part).group(1))
            # The first price wins, should another table on the page name the model again.
            (current["long_prompt"] if tier == "long" else current).setdefault(label, price)
            if tier is None:
                label = None            # one price per label outside a tiered model
    return {name: prices for name, prices in models.items() if prices}


def compare(table: dict, page: dict) -> list[str]:
    """What differs, one line each. Fields the page doesn't show are left alone,
    except the 1-hour write, checked against twice input."""
    lines = []
    for name, ours in table.items():
        theirs = page.get(name)
        if theirs is None:
            lines.append(f"{name}: not on the page")
            continue
        for scope, mine, listed in (("", ours, theirs),
                                    ("long_prompt.", ours.get("long_prompt"), theirs.get("long_prompt"))):
            if mine is None and listed is None:
                continue
            if mine is None or listed is None:
                lines.append(f"{name}: {'the page' if listed else 'prices.json'} has a long prompt tier "
                             f"and {'prices.json' if listed else 'the page'} doesn't")
                continue
            for field, price in listed.items():
                if isinstance(price, dict):
                    continue            # the long prompt tier, checked on its own
                if mine.get(field) != price:
                    lines.append(f"{name}: {scope}{field} is {mine.get(field)} here, {price} on the page")
            if "input" in mine and mine.get("cache_write_1h") != 2 * mine["input"]:
                lines.append(f"{name}: {scope}cache_write_1h is {mine.get('cache_write_1h')}, "
                             f"not twice input ({2 * mine['input']})")
    return lines


def apply(table: dict, page: dict) -> dict:
    """The table with the page's prices for every model it already lists."""
    updated = json.loads(json.dumps(table))
    for name, ours in updated.items():
        theirs = page.get(name) or {}
        for scope, listed in ((ours, theirs), (ours.get("long_prompt"), theirs.get("long_prompt"))):
            if scope is not None and listed:
                scope.update({field: price for field, price in listed.items()
                              if not isinstance(price, dict)})
                if "input" in scope:
                    scope["cache_write_1h"] = 2 * scope["input"]
    return updated


def fetch() -> str:
    request = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (sssf check_prices)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def main(argv: list[str] | None = None, read=fetch, out=sys.stdout) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="apply the page's prices to prices.json")
    parser.add_argument("--table", type=Path, default=TABLE, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    data = json.loads(args.table.read_text())
    try:
        page = parse(read())
    except OSError as error:
        print(f"couldn't read {URL}: {error}", file=out)
        return 2
    if not any(name in page for name in data["models"]):
        print(f"{URL} holds none of prices.json's models, so its layout has probably changed. "
              "Compare it by hand.", file=out)
        return 2

    # A model priced from another page, named in "elsewhere", is checked by hand.
    elsewhere = data.get("elsewhere", {})
    checked = {name: prices for name, prices in data["models"].items() if name not in elsewhere}
    if elsewhere:
        print(f"not checked, priced from another page: {', '.join(sorted(elsewhere))}", file=out)
    differences = compare(checked, page)
    unlisted = sorted(set(page) - set(data["models"]))
    if unlisted:
        print(f"on the page, not in prices.json (add one only if SSSF agents use it): "
              f"{', '.join(unlisted)}", file=out)
    if not differences:
        print(f"prices.json matches {URL} for its {len(data['models'])} models.", file=out)
        return 0
    print("prices.json differs from the page:", file=out)
    for line in differences:
        print(f"  {line}", file=out)
    if not args.write:
        print("Run again with --write to apply the page's prices.", file=out)
        return 1
    data["models"] = {**data["models"], **apply(checked, page)}
    data["source"] = re.sub(r"read \d{4}-\d{2}-\d{2}", f"read {datetime.date.today().isoformat()}",
                            data["source"])
    args.table.write_text(json.dumps(data, indent=1) + "\n")
    left = compare({n: v for n, v in data["models"].items() if n not in elsewhere}, page)
    print(f"wrote {args.table}. Commit it and update the repos." if not left
          else "wrote the table, but these still differ:\n  " + "\n  ".join(left), file=out)
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main())
