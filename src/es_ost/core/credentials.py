"""Developer credentials stored base64 in an embedded ``rc_data`` env resource.

The app ships ``devid``/``devpassword``/``softname`` base64-encoded so the
registered app credentials are not plaintext. Users always supply
their own ScreenScraper ``ssid``/``sspassword`` separately.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Mapping

DEVID_KEY = "DEVID"
DEVPASSWORD_KEY = "DEVPASSWORD"
SOFTNAME_KEY = "SOFTNAME"


@dataclass(frozen=True, slots=True)
class DevCredentials:
    devid: str
    devpassword: str
    softname: str


def _encode(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _decode(value: str) -> str:
    try:
        return base64.b64decode(value.encode("ascii")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - keep the error actionable
        raise ValueError(f"invalid base64 in rc_data: {value!r}") from exc


def encode_rc_data(devid: str, devpassword: str, softname: str) -> str:
    """Render an rc_data env file with base64-encoded values."""
    return "\n".join(
        (
            f"{DEVID_KEY}={_encode(devid)}",
            f"{DEVPASSWORD_KEY}={_encode(devpassword)}",
            f"{SOFTNAME_KEY}={_encode(softname)}",
        )
    ) + "\n"


def decode_rc_data(text: str) -> DevCredentials:
    """Parse an rc_data env file back into DevCredentials."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = _decode(value.strip())
    return DevCredentials(
        devid=values.get(DEVID_KEY, ""),
        devpassword=values.get(DEVPASSWORD_KEY, ""),
        softname=values.get(SOFTNAME_KEY, ""),
    )


def dev_credentials_from_env(env: Mapping[str, str] | None = None) -> DevCredentials:
    """Read DevCredentials from an environment mapping (default: os.environ)."""
    if env is None:
        import os

        env = os.environ
    return DevCredentials(
        devid=_decode(env.get(DEVID_KEY, "")),
        devpassword=_decode(env.get(DEVPASSWORD_KEY, "")),
        softname=_decode(env.get(SOFTNAME_KEY, "")),
    )
