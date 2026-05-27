"""Vendored third-party assets.

Readability.js — Mozilla's content extractor (powers Firefox Reader View).
    version: 0.6.0
    sha:     04fd32f72b448c12b02ba6c40928b67e510bac49
    source:  https://github.com/mozilla/readability
    license: Apache 2.0 (see ./LICENSE)

To upgrade: download Readability.js + LICENSE.md from the desired tagged
release and overwrite both files; bump the version/sha constants below.
"""

from pathlib import Path

VENDOR_DIR = Path(__file__).parent
READABILITY_JS_PATH = VENDOR_DIR / "readability.js"
READABILITY_VERSION = "0.6.0"
READABILITY_SHA = "04fd32f72b448c12b02ba6c40928b67e510bac49"
