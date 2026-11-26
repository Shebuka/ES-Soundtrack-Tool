# Nuitka / pyside6-deploy build instructions. pyside6-deploy reads this file
# (the spec's input_file) and Nuitka reads these directives from the main
# script it compiles, so the data and asset includes must live here rather than
# in the package modules.
#
# nuitka-project: --standalone
# nuitka-project: --output-filename=es-soundtrack-tool
# nuitka-project: --python-flag=no_asserts
# nuitka-project: --python-flag=no_docstrings
# nuitka-project: --include-data-dir=data=data
# nuitka-project: --include-data-dir=assets=assets
# The scrape cache lives under data/ but is hundreds of MB and must never
# ship in a release.
# nuitka-project: --noinclude-data-files=data/.cache/*
# nuitka-project: --noinclude-data-files=data/.DS_Store
# On macOS the dependency walker also picks up a newer OpenSSL keg and copies
# it in. Only libcrypto.3/libssl.3 are linked, by _ssl and _hashlib; nothing
# references the .4 pair or the unversioned aliases, so they are dead weight.
# nuitka-project: --noinclude-dlls=libcrypto.4.dylib
# nuitka-project: --noinclude-dlls=libssl.4.dylib
# nuitka-project: --noinclude-dlls=libcrypto.dylib
# nuitka-project: --noinclude-dlls=libssl.dylib

# nuitka-project-if: {OS} == "Windows":
#     nuitka-project: --file-description="ES Soundtrack Tool"
#     nuitka-project: --file-version=0.1.0
#     nuitka-project: --product-name="ES Soundtrack Tool"
#     nuitka-project: --product-version=0.1.0
#     nuitka-project: --copyright="Shebuka"
#     nuitka-project: --windows-console-mode=disable
#     The directory include above silently skips executables, so ffmpeg.exe has
#     to be named explicitly or the packaged app cannot export a single track.
#     nuitka-project: --include-data-files=assets/ffmpeg/ffmpeg.exe=assets/ffmpeg/ffmpeg.exe

"""Launcher for the frozen ``es-soundtrack-tool`` build.

The package entry point in ``pyproject.toml`` still points at
``es_ost.gui.app:main``; this file exists because pyside6-deploy compiles a
single script, and a script inside the package cannot use relative imports
when it is run as ``__main__``.
"""

from __future__ import annotations

from es_ost.gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
