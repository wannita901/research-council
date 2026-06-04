"""Load a RunConfig from YAML + apply CLI overrides."""

from __future__ import annotations

from pathlib import Path

import yaml

from research_council.store.models import RunConfig

CONFIG_DIR = Path(__file__).parent / "config"


def load_config(stage: str = "ideation") -> RunConfig:
    path = CONFIG_DIR / f"{stage}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return RunConfig(**(data or {}))


def parse_seats(spec: str) -> dict[str, str]:
    # "openai=gpt-5,anthropic=claude-opus-4-8" -> {...}
    out: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        vendor, _, model = part.partition("=")
        out[vendor.strip()] = model.strip()
    return out


def parse_tools(spec: str) -> list[str]:
    return [t.strip() for t in spec.split(",") if t.strip()]
