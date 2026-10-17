"""BRAND-1's single source: read the build brand out of the version header.

The branding tests used to hard-code `PDT-GN v1`. That copy has to move with the release
version now (L0 BRAND-6: the brand version *is* the release tag), and a hard-coded string
would keep passing after a version bump - it would be asserting last release's brand while
the binary says something else. So both tests parse the header instead:

    source/blender/blenkernel/BKE_blender_version.h

    #define BLENDER_BRAND_FAMILY  "PDT-GN"
    #define BLENDER_BRAND_VERSION "v0.1.0"
    #define BLENDER_BRAND_ID      BLENDER_BRAND_FAMILY " " BLENDER_BRAND_VERSION

Both layouts we run in are handled by walking up from this file: the fork
(`tools/pdt/tests/`) and the exported artifact (`files/tools/pdt/tests/`).

Raises `BrandSourceError` when the header cannot be found or does not carry the three
defines - a test that cannot see the source of truth must fail loudly, not skip.
"""

from __future__ import annotations

import os
import re

HEADER_REL = os.path.join("source", "blender", "blenkernel", "BKE_blender_version.h")

#: `FileGlobal.build_hash` is `char[16]`, so the marker has exactly 15 usable bytes.
MAX_BRAND_BYTES = 15


class BrandSourceError(RuntimeError):
    pass


def find_repo_root() -> str:
    """The directory that contains `source/blender/...`, or raise."""
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(6):
        if os.path.exists(os.path.join(here, HEADER_REL)):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    # Fallback: the harness usually starts in the repo root.
    here = os.getcwd()
    for _ in range(6):
        if os.path.exists(os.path.join(here, HEADER_REL)):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            break
        here = parent
    raise BrandSourceError(
        f"cannot find {HEADER_REL} above {os.path.dirname(os.path.abspath(__file__))} "
        f"or {os.getcwd()}"
    )


def _define(text: str, name: str) -> str:
    match = re.search(rf'^\s*#define\s+{name}\s+"([^"]*)"', text, re.MULTILINE)
    if not match:
        raise BrandSourceError(f'no #define {name} "..." in the version header')
    return match.group(1)


def read_brand() -> tuple[str, str, str]:
    """Return `(brand, family, version)`, e.g. `("PDT-GN v0.1.0", "PDT-GN", "v0.1.0")`."""
    path = os.path.join(find_repo_root(), HEADER_REL)
    with open(path, "r", encoding="utf-8") as handle:
        text = handle.read()
    family = _define(text, "BLENDER_BRAND_FAMILY")
    version = _define(text, "BLENDER_BRAND_VERSION")
    brand = f"{family} {version}"
    if 'BLENDER_BRAND_ID BLENDER_BRAND_FAMILY " " BLENDER_BRAND_VERSION' not in text:
        raise BrandSourceError(
            "BLENDER_BRAND_ID is no longer composed from the family and version - update "
            "this reader and tools/pdt/version.ps1 together"
        )
    return brand, family, version
