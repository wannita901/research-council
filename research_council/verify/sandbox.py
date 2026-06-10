"""Execution sandbox (plan/14) — the deferred "run-it" verifier for Stage B.

Runs a generated experiment script and reports whether it executed and emitted a metric.
Two backends behind one tiny interface:
  • DockerSandbox — isolated: `docker run --network none --memory … timeout N python`. Default.
  • LocalSandbox  — a bare subprocess in a temp dir. NOT isolated; runs code on this machine,
    so it's opt-in only (use for trusted/test code or when you accept the risk).

`build_sandbox` prefers Docker and refuses to silently fall back to local unless allowed.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SandboxResult:
    ok: bool          # exit_code == 0 and not timed out
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    backend: str = ""


def _write(code: str, d: str) -> None:
    (Path(d) / "experiment.py").write_text(code, encoding="utf-8")


class LocalSandbox:
    """Subprocess in a temp dir with a timeout. UNISOLATED — only for trusted/test code."""

    name = "local"

    def run(self, code: str, *, timeout: int = 30) -> SandboxResult:
        with tempfile.TemporaryDirectory() as d:
            _write(code, d)
            t0 = time.monotonic()
            try:
                p = subprocess.run(["python", "experiment.py"], cwd=d, capture_output=True,
                                   text=True, timeout=timeout)
                return SandboxResult(p.returncode == 0, p.returncode, p.stdout, p.stderr,
                                     time.monotonic() - t0, False, "local")
            except subprocess.TimeoutExpired as e:
                return SandboxResult(False, -1, e.stdout or "", (e.stderr or "") + "\n[timeout]",
                                     time.monotonic() - t0, True, "local")


class DockerSandbox:
    """Isolated run in a throwaway container (no network, capped memory/cpu, hard timeout)."""

    name = "docker"

    def __init__(self, image: str = "python:3.14-slim", memory: str = "512m", network: str = "none"):
        self.image, self.memory, self.network = image, memory, network

    def run(self, code: str, *, timeout: int = 30) -> SandboxResult:
        with tempfile.TemporaryDirectory() as d:
            _write(code, d)
            cmd = ["docker", "run", "--rm", "--network", self.network, "--memory", self.memory,
                   "--cpus", "1", "--pids-limit", "256", "-v", f"{d}:/work:ro", "-w", "/work",
                   self.image, "timeout", str(timeout), "python", "experiment.py"]
            t0 = time.monotonic()
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 20)
                timed = p.returncode == 124  # `timeout` exit code
                return SandboxResult(p.returncode == 0, p.returncode, p.stdout, p.stderr,
                                     time.monotonic() - t0, timed, "docker")
            except subprocess.TimeoutExpired as e:
                return SandboxResult(False, -1, e.stdout or "", (e.stderr or "") + "\n[docker timeout]",
                                     time.monotonic() - t0, True, "docker")


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=6).returncode == 0
    except Exception:
        return False


# Our experiment image (built via `mise run build-image`) preinstalls the scientific stack so
# the council can run real-ish experiments offline; we fall back to bare python if it's absent.
EXPERIMENT_IMAGE = "research-council-exp:latest"
_FALLBACK_IMAGE = "python:3.14-slim"


def _image_present(name: str) -> bool:
    try:
        return subprocess.run(["docker", "image", "inspect", name],
                              capture_output=True, timeout=6).returncode == 0
    except Exception:
        return False


def best_experiment_image() -> tuple[str, bool]:
    """(image, has_scientific_stack). Prefer our prebuilt image; else bare python (stdlib-only)."""
    if _image_present(EXPERIMENT_IMAGE):
        return EXPERIMENT_IMAGE, True
    return _FALLBACK_IMAGE, False


def build_sandbox(prefer: str = "docker", *, allow_local: bool = False):
    """Return (sandbox, warning). Docker preferred; local only if explicitly allowed
    (it runs code unsandboxed). Returns (None, reason) if nothing safe is available."""
    if prefer == "docker" and docker_available():
        image, _ = best_experiment_image()
        return DockerSandbox(image=image), None
    if allow_local:
        return LocalSandbox(), ("⚠ Docker unavailable — using the LOCAL sandbox, which runs "
                                "generated code UNISOLATED on this machine.")
    return None, ("no isolated sandbox: Docker not available. Install/start Docker, or pass "
                  "--allow-local-sandbox to run unsandboxed (unsafe).")
