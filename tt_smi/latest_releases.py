# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""Fetches the golden version pins for tt-stack packages.

The source of truth is `golden.json` from the latest tt-sw-manifest release:
a set of component versions that have been validated together as one stack.
This is deliberately *not* each repo's newest upstream release, which may not
yet be approved. Each package maps to one pin field in that manifest.
"""

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ReleaseSpec:
    name: str
    pin: str
    # cli=None means there's no PATH-based check for this package (firmware,
    # metalium image, etc.). tt-kmd has no CLI but is checkable via sysfs, so
    # we leave cli=None and special-case it in get_installed_version.
    cli: Optional[str] = None


RELEASE_SPECS: List[ReleaseSpec] = [
    ReleaseSpec("tt-kmd",             "kmd"),
    ReleaseSpec("tt-smi",             "smi",            cli="tt-smi"),
    ReleaseSpec("tt-flash",           "flash",          cli="tt-flash"),
    ReleaseSpec("tt-system-firmware", "firmware"),
    ReleaseSpec("tt-metal",           "metal-version"),
    ReleaseSpec("tt-installer",       "installer"),
]

GOLDEN_MANIFEST_URL = "https://github.com/tenstorrent/tt-sw-manifest/releases/latest/download/golden.json"


def is_checkable(spec: ReleaseSpec) -> bool:
    """Whether we can determine 'installed or not' for this spec via the host."""
    return spec.name == "tt-kmd" or spec.cli is not None


def _read_kmd_version() -> Optional[str]:
    try:
        with open("/sys/module/tenstorrent/version") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _cli_version(cli: str) -> Optional[str]:
    if shutil.which(cli) is None:
        return None
    try:
        result = subprocess.run(
            [cli, "--version"], capture_output=True, text=True, timeout=2
        )
    except (subprocess.SubprocessError, OSError):
        return None
    output = (result.stdout or "") + (result.stderr or "")
    m = re.search(r"\d+\.\d+\.\d+", output)
    return m.group(0) if m else None


def get_installed_version(spec: ReleaseSpec) -> Optional[str]:
    """Look up the locally-installed version of a checkable package.

    Returns the version string, or None if checkable but not installed. Caller
    should gate on is_checkable() before reading the absence-of-key as "missing".
    """
    if spec.name == "tt-kmd":
        return _read_kmd_version()
    if spec.cli is not None:
        return _cli_version(spec.cli)
    return None


def get_installed_all() -> Dict[str, Optional[str]]:
    """Best-effort installed-version lookup for every checkable spec.

    Returns {package_name: version_or_None}, only for specs where we have a
    way to check. Specs not in the returned dict are "not checkable" (e.g.
    tt-system-firmware, tt-metal) and should render without a status glyph.
    """
    return {
        spec.name: get_installed_version(spec)
        for spec in RELEASE_SPECS
        if is_checkable(spec)
    }


def version_tuple(v: str) -> Tuple[int, ...]:
    """Best-effort semver-ish tuple for comparison. Non-numeric parts are skipped."""
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            break
    return tuple(parts)


def _fetch_golden(timeout: float) -> Optional[Dict[str, str]]:
    """Return the golden manifest pins, or None if the fetch/parse failed."""
    req = urllib.request.Request(
        GOLDEN_MANIFEST_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "tt-smi",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            manifest = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def _pinned_version(manifest: Dict[str, str], spec: ReleaseSpec) -> Optional[str]:
    """Pull one spec's version out of the manifest, normalized to bare semver.

    Pins are written as plain versions except metal-version, which carries the
    upstream "v" tag prefix.
    """
    pinned = manifest.get(spec.pin)
    if not isinstance(pinned, str):
        return None
    return pinned.removeprefix("v") or None


DEFAULT_MAX_ATTEMPTS = 3


def fetch_all(
    timeout: float = 5.0,
    on_done: Optional[Callable[[ReleaseSpec, Optional[str]], None]] = None,
    on_attempt: Optional[Callable[[int], None]] = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Dict[str, Optional[str]]:
    """Fetch the golden pin for every spec, retrying the manifest on failure.

    All pins come from a single manifest, so one fetch resolves every spec.
    `on_done` fires exactly once per spec, with its pinned version or None if
    the manifest could not be fetched (or has no pin for it). `on_attempt(n)`
    fires at the start of attempts where n > 1 so callers can surface a retry
    indicator; it is not called for the first attempt.
    """
    manifest = None
    for attempt in range(1, max_attempts + 1):
        if attempt > 1 and on_attempt is not None:
            try:
                on_attempt(attempt)
            except Exception:
                pass

        manifest = _fetch_golden(timeout)
        if manifest is not None:
            break

    final: Dict[str, Optional[str]] = {}
    for spec in RELEASE_SPECS:
        version = _pinned_version(manifest, spec) if manifest is not None else None
        final[spec.name] = version
        if on_done is not None:
            try:
                on_done(spec, version)
            except Exception:
                pass
    return final
