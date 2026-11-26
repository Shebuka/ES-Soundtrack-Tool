"""The packaged app must carry its own CA bundle.

A frozen interpreter keeps the CA search path it was compiled with, which is the
build machine's, so trusting the defaults means every HTTPS request fails on a
user's computer. These assertions are what the bundle relies on.
"""

from __future__ import annotations

import ssl

from es_ost import tls


def test_context_verifies_certificates_and_hostnames():
    context = tls.ssl_context()

    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_context_carries_root_certificates():
    # The point of the module: roots come from certifi's file, not from whatever
    # ssl's compiled-in defaults point at.
    assert tls.ssl_context().get_ca_certs()


def test_context_is_built_once():
    assert tls.ssl_context() is tls.ssl_context()
