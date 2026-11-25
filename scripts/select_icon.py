"""Point ``pysidedeploy.spec`` at the app icon format this OS needs.

Nuitka takes a single icon path per platform and the formats are not
interchangeable: macOS wants ``.icns``, Windows ``.ico`` and Linux a ``.png``.
The three exports live in ``assets/icons``; run this before ``pyside6-deploy``
(the release workflow does the same for each target).

    python scripts/select_icon.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ICONS = {
    "darwin": "assets/icons/soundtrack.icns",
    "win32": "assets/icons/soundtrack.ico",
}
DEFAULT_ICON = "assets/icons/soundtrack.png"
SPEC = Path("pysidedeploy.spec")


def icon_for_current_platform() -> str:
    return ICONS.get(sys.platform, DEFAULT_ICON)


def main() -> int:
    icon = icon_for_current_platform()
    if not Path(icon).is_file():
        raise SystemExit(f"missing icon file {icon}")
    text = SPEC.read_text(encoding="utf-8")
    updated, count = re.subn(r"(?m)^icon = .*$", f"icon = {icon}", text)
    if count != 1:
        raise SystemExit("pysidedeploy.spec has no unique 'icon = ...' line")
    SPEC.write_text(updated, encoding="utf-8")
    print(f"icon set to {icon}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
