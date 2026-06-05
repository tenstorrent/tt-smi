# SPDX-FileCopyrightText: © 2026 Tenstorrent Inc.
# SPDX-License-Identifier: Apache-2.0

"""Fetches the latest published GitHub release tag for tt-stack packages.

Each package has a tag pattern with a named "version" group; only releases
whose tag matches the pattern are considered. This mirrors the renovate
filter rules used by infra (allowedVersions / extractVersion).
"""

import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class ReleaseSpec:
    name: str
    repo: str
    tag_pattern: "re.Pattern[str]"
    # cli=None means there's no PATH-based check for this package (firmware,
    # metalium image, etc.). tt-kmd has no CLI but is checkable via sysfs, so
    # we leave cli=None and special-case it in get_installed_version.
    cli: Optional[str] = None


RELEASE_SPECS: List[ReleaseSpec] = [
    ReleaseSpec("tt-kmd",             "tenstorrent/tt-kmd",             re.compile(r"^ttkmd-?(?P<version>\d+\.\d+\.\d+)$")),
    ReleaseSpec("tt-smi",             "tenstorrent/tt-smi",             re.compile(r"^v?(?P<version>\d+\.\d+\.\d+)$"),       cli="tt-smi"),
    ReleaseSpec("tt-flash",           "tenstorrent/tt-flash",           re.compile(r"^v?(?P<version>\d+\.\d+\.\d+)$"),       cli="tt-flash"),
    ReleaseSpec("tt-system-firmware", "tenstorrent/tt-system-firmware", re.compile(r"^v?(?P<version>19\.\d+\.\d+)$")),
    ReleaseSpec("tt-metal",           "tenstorrent/tt-metal",           re.compile(r"^v?(?P<version>0\.\d+\.\d+)$")),
    ReleaseSpec("tt-installer",       "tenstorrent/tt-installer",       re.compile(r"^v?(?P<version>\d+\.\d+\.\d+)$")),
]


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


def _fetch_latest_matching(spec: ReleaseSpec, timeout: float) -> Optional[str]:
    """Return the version string of the newest release matching spec, or None."""
    url = f"https://api.github.com/repos/{spec.repo}/releases?per_page=100"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "tt-smi",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            releases = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None

    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        m = spec.tag_pattern.match(release.get("tag_name", ""))
        if m:
            return m.group("version")
    return None


DEFAULT_MAX_ATTEMPTS = 3


def _fetch_batch(
    specs: List[ReleaseSpec], timeout: float
) -> Dict[str, Optional[str]]:
    """Run one parallel pass over `specs`. Returns {name: version_or_None}."""
    results: Dict[str, Optional[str]] = {}
    if not specs:
        return results
    with ThreadPoolExecutor(max_workers=len(specs)) as pool:
        futures = {
            pool.submit(_fetch_latest_matching, spec, timeout): spec for spec in specs
        }
        for fut in as_completed(futures):
            spec = futures[fut]
            try:
                results[spec.name] = fut.result()
            except Exception:
                results[spec.name] = None
    return results


def fetch_all(
    timeout: float = 5.0,
    on_done: Optional[Callable[[ReleaseSpec, Optional[str]], None]] = None,
    on_attempt: Optional[Callable[[int], None]] = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> Dict[str, Optional[str]]:
    """Fetch latest versions for every spec, retrying any that come back None.

    Each attempt runs the remaining failed specs in parallel. `on_done` fires
    exactly once per spec, when its version is known (success) or has exhausted
    all attempts (final None). `on_attempt(n)` fires at the start of attempts
    where n > 1 so callers can surface a retry indicator; it is not called for
    the first attempt.
    """
    pending = list(RELEASE_SPECS)
    final: Dict[str, Optional[str]] = {}
    for attempt in range(1, max_attempts + 1):
        if attempt > 1 and on_attempt is not None:
            try:
                on_attempt(attempt)
            except Exception:
                pass

        round_results = _fetch_batch(pending, timeout)

        next_pending: List[ReleaseSpec] = []
        for spec in pending:
            version = round_results.get(spec.name)
            if version is not None or attempt == max_attempts:
                final[spec.name] = version
                if on_done is not None:
                    try:
                        on_done(spec, version)
                    except Exception:
                        pass
            else:
                next_pending.append(spec)
        pending = next_pending
        if not pending:
            break
    return final
