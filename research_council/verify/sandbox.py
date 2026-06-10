"""Execution sandbox (plan/14 + plan/24) — the "run-it" verifier for Stage B.

Runs a generated experiment script and reports whether it executed and emitted a metric.
The experiment may declare pip `requirements`: they're installed in a network-enabled PREP
step, then the script runs with **no network** — real libraries, isolated execution. Any
figures the script saves (top-level or in `figures/`) are collected back as bytes.

Two backends behind one tiny interface:
  • DockerSandbox — isolated: install (network) → `docker run --network none … python`. Default.
  • LocalSandbox  — bare subprocess in a temp dir. NOT isolated; opt-in only (trusted/test code).

`build_sandbox` prefers Docker and refuses to silently fall back to local unless allowed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

# Figure files the experiment may emit (top-level or under figures/), collected as bytes.
_FIGURE_GLOBS = ("*.png", "*.pdf", "*.svg", "figures/*.png", "figures/*.pdf", "figures/*.svg")
_MAX_FIGURE_BYTES = 8_000_000
_INSTALL_TIMEOUT = 300


@dataclass
class SandboxResult:
    ok: bool  # exit_code == 0 and not timed out
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    backend: str = ""
    figures: dict[str, bytes] = field(default_factory=dict)  # filename -> bytes (collected plots)


def _write(code: str, d: Path) -> None:
    (d / "experiment.py").write_text(code, encoding="utf-8")


def _collect_figures(workdir: Path) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    for pattern in _FIGURE_GLOBS:
        for p in sorted(workdir.glob(pattern)):
            if p.is_file() and p.stat().st_size <= _MAX_FIGURE_BYTES:
                out[p.name] = p.read_bytes()  # flat by basename (figures/x.png -> x.png)
    return out


class LocalSandbox:
    """Subprocess in a temp dir with a timeout. UNISOLATED — only for trusted/test code."""

    name = "local"

    def run(
        self, code: str, *, timeout: int = 30, requirements: list[str] | None = None
    ) -> SandboxResult:
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            _write(code, work)
            env = os.environ.copy()
            if requirements:
                site = work / ".deps"
                r = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--quiet",
                        "--target",
                        str(site),
                        *requirements,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=_INSTALL_TIMEOUT,
                )
                if r.returncode != 0:
                    return SandboxResult(
                        False,
                        r.returncode,
                        r.stdout,
                        "pip install failed:\n" + (r.stderr or "")[-1500:],
                        0.0,
                        False,
                        "local",
                    )
                env["PYTHONPATH"] = str(site) + os.pathsep + env.get("PYTHONPATH", "")
            t0 = time.monotonic()
            try:
                p = subprocess.run(
                    ["python", "experiment.py"],
                    cwd=str(work),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env=env,
                )
                return SandboxResult(
                    p.returncode == 0,
                    p.returncode,
                    p.stdout,
                    p.stderr,
                    time.monotonic() - t0,
                    False,
                    "local",
                    _collect_figures(work),
                )
            except subprocess.TimeoutExpired as e:
                return SandboxResult(
                    False,
                    -1,
                    e.stdout or "",
                    (e.stderr or "") + "\n[timeout]",
                    time.monotonic() - t0,
                    True,
                    "local",
                )


class DockerSandbox:
    """Isolated run in a throwaway container (no network, capped memory/cpu, hard timeout).
    Requirements are pip-installed in a separate network-enabled step before the isolated run."""

    name = "docker"

    def __init__(
        self, image: str = "python:3.14-slim", memory: str = "512m", network: str = "none"
    ):
        self.image, self.memory, self.network = image, memory, network

    def _caps(self) -> list[str]:
        return ["--memory", self.memory, "--cpus", "1", "--pids-limit", "256"]

    def run(
        self, code: str, *, timeout: int = 30, requirements: list[str] | None = None
    ) -> SandboxResult:
        with tempfile.TemporaryDirectory() as d:
            work = Path(d) / "work"
            work.mkdir()
            _write(code, work)
            site = Path(d) / "site"
            site.mkdir()

            # PREP: install requirements with network ON (in a throwaway container).
            if requirements:
                inst = [
                    "docker",
                    "run",
                    "--rm",
                    *self._caps(),
                    "-v",
                    f"{site}:/site",
                    "-w",
                    "/site",
                    self.image,
                    "pip",
                    "install",
                    "--no-cache-dir",
                    "--target",
                    "/site",
                    *requirements,
                ]
                try:
                    ri = subprocess.run(
                        inst, capture_output=True, text=True, timeout=_INSTALL_TIMEOUT
                    )
                except subprocess.TimeoutExpired:
                    return SandboxResult(
                        False, -1, "", "pip install timed out", 0.0, False, "docker"
                    )
                if ri.returncode != 0:
                    return SandboxResult(
                        False,
                        ri.returncode,
                        ri.stdout,
                        "pip install failed:\n" + (ri.stderr or "")[-1500:],
                        0.0,
                        False,
                        "docker",
                    )

            # RUN: the experiment with NO network. The installed packages mount read-only.
            mounts = ["-v", f"{work}:/work", "-w", "/work"]
            envs: list[str] = []
            if requirements:
                mounts += ["-v", f"{site}:/site:ro"]
                envs = ["-e", "PYTHONPATH=/site"]
            cmd = [
                "docker",
                "run",
                "--rm",
                "--network",
                self.network,
                *self._caps(),
                *mounts,
                *envs,
                self.image,
                "timeout",
                str(timeout),
                "python",
                "experiment.py",
            ]
            t0 = time.monotonic()
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 20)
                timed = p.returncode == 124  # `timeout` exit code
                return SandboxResult(
                    p.returncode == 0,
                    p.returncode,
                    p.stdout,
                    p.stderr,
                    time.monotonic() - t0,
                    timed,
                    "docker",
                    _collect_figures(work),
                )
            except subprocess.TimeoutExpired as e:
                return SandboxResult(
                    False,
                    -1,
                    e.stdout or "",
                    (e.stderr or "") + "\n[docker timeout]",
                    time.monotonic() - t0,
                    True,
                    "docker",
                )


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=6).returncode == 0
    except Exception:
        return False


# Our experiment image (built via `mise run build-image`) preinstalls the scientific stack so
# common deps are instant; declared requirements are still installed on top per experiment.
EXPERIMENT_IMAGE = "research-council-exp:latest"
_FALLBACK_IMAGE = "python:3.14-slim"


def _image_present(name: str) -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "image", "inspect", name], capture_output=True, timeout=6
            ).returncode
            == 0
        )
    except Exception:
        return False


def best_experiment_image() -> tuple[str, bool]:
    """(image, has_scientific_stack). Prefer our prebuilt image; else bare python."""
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
        return LocalSandbox(), (
            "⚠ Docker unavailable — using the LOCAL sandbox, which runs "
            "generated code UNISOLATED on this machine."
        )
    return None, (
        "no isolated sandbox: Docker not available. Install/start Docker, or pass "
        "--allow-local-sandbox to run unsandboxed (unsafe)."
    )
