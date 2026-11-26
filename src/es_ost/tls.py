"""TLS trust for a frozen build.

CPython's :mod:`ssl` trusts the CA file path compiled into the interpreter. In a
Nuitka bundle that path is the *build* machine's — Homebrew's ``openssl@3`` keg
on the macOS runner, the toolcache directory on Windows — so a packaged app
finds no roots at all and every HTTPS request fails with
``CERTIFICATE_VERIFY_FAILED``. macOS makes it worse: CPython never consults the
Keychain, so there is no system fallback either.

certifi ships its own ``cacert.pem`` inside the wheel. ``main.py`` bundles that
file as package data and every request in the app goes through the context
built here, so the trust store travels with the app instead of depending on the
machine it was compiled on.
"""

from __future__ import annotations

import ssl
from functools import lru_cache

import certifi


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    """TLS context backed by the certificate bundle shipped with the app.

    Cached because a context carries parsed keys and building one per request
    would re-read the file for nothing. Passing ``cafile`` also stops
    ``create_default_context`` from loading the build machine's default paths.
    """
    return ssl.create_default_context(cafile=certifi.where())
