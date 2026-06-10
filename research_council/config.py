"""Load a RunConfig from YAML + apply CLI overrides."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from research_council.store.models import RunConfig

CONFIG_DIR = Path(__file__).parent / "config"

# Per-vendor model can be set via env (mise.toml [env]); overrides the yaml default.
ENV_MODEL = {"openai": "RC_OPENAI_MODEL", "anthropic": "RC_ANTHROPIC_MODEL", "gemini": "RC_GEMINI_MODEL"}


def load_config(stage: str = "ideation", profile: str | None = None) -> RunConfig:
    path = CONFIG_DIR / f"{stage}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    cfg = RunConfig(**(data or {}))
    # precedence: yaml default < env (RC_*) < --seats CLI flag (applied later)
    for vendor, env in ENV_MODEL.items():
        val = os.getenv(env)
        if val and vendor in cfg.seats:
            cfg.seats[vendor] = val
    fac = os.getenv("RC_FACILITATOR_MODEL")
    if fac:
        cfg.facilitator_model = fac
    # Stage-A caps now scale with RC_PROFILE (or --profile); per-field RC_MAX_* still wins.
    from research_council.debate.caps import stage_a_caps
    for field, value in stage_a_caps(profile).items():
        setattr(cfg, field, value)
    return cfg


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
