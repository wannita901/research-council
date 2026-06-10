"""Load a RunConfig from YAML + apply CLI overrides."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from research_council.store.models import RunConfig

CONFIG_DIR = Path(__file__).parent / "config"

# Per-vendor model can be set via env (mise.toml [env]); overrides the yaml default.
ENV_MODEL = {"openai": "RC_OPENAI_MODEL", "anthropic": "RC_ANTHROPIC_MODEL", "gemini": "RC_GEMINI_MODEL"}

# Stage-A (ideation) caps → env var. All surfaced in mise.toml for easy tuning.
ENV_CAP_INT = {
    "max_iters": "RC_MAX_ITERS", "max_tool_calls": "RC_MAX_TOOL_CALLS",
    "max_turns": "RC_MAX_TURNS", "max_rounds": "RC_MAX_ROUNDS",
    "max_msgs_per_peer": "RC_MAX_MSGS_PER_PEER",
}
ENV_CAP_FLOAT = {"usd_max": "RC_USD_MAX"}


def _env_int(name: str) -> int | None:
    v = os.getenv(name)
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def _env_float(name: str) -> float | None:
    v = os.getenv(name)
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def load_config(stage: str = "ideation") -> RunConfig:
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
    for field, env in ENV_CAP_INT.items():
        v = _env_int(env)
        if v is not None:
            setattr(cfg, field, v)
    for field, env in ENV_CAP_FLOAT.items():
        v = _env_float(env)
        if v is not None:
            setattr(cfg, field, v)
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
