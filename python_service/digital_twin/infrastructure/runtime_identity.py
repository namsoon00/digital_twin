"""Stable deployment identity for ontology operational diagnostics.

This metadata identifies the process that produced an operational audit row.
It is deliberately excluded from ABox facts and RuleBox evaluation.
"""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import subprocess
import sys
from typing import Dict


ROOT_DIR = Path(__file__).resolve().parents[3]


def _environment_value(*names: str) -> str:
    for name in names:
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


@lru_cache(maxsize=1)
def runtime_identity() -> Dict[str, str]:
    """Return a cheap, non-sensitive deployment identity once per process."""

    version = _environment_value("ORBIT_RUNTIME_VERSION", "SOURCE_VERSION") or "local-development"
    revision = _environment_value("ORBIT_RUNTIME_REVISION", "GIT_SHA", "GITHUB_SHA")
    source = "environment" if revision else ""
    if not revision:
        try:
            completed = subprocess.run(
                ["git", "rev-parse", "--short=12", "HEAD"],
                cwd=ROOT_DIR,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=1,
                check=False,
            )
            revision = completed.stdout.strip()
            source = "git" if revision else ""
        except (OSError, subprocess.SubprocessError):
            pass
    return {
        "contract": "orbit-runtime-identity-v1",
        "version": version,
        "revision": revision or "unknown",
        "source": source or "unavailable",
        "python": sys.version.split()[0],
    }
