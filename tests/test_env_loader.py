import base64
import os

from es_ost.core import env_loader
from es_ost.core.credentials import DevCredentials, dev_credentials_from_env


def _encoded_env(devid="a", devpassword="b", softname="c") -> dict[str, str]:
    def b64(value: str) -> str:
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    return {
        "DEVID": b64(devid),
        "DEVPASSWORD": b64(devpassword),
        "SOFTNAME": b64(softname),
    }


def test_dev_credentials_from_env():
    assert dev_credentials_from_env(_encoded_env()) == DevCredentials("a", "b", "c")


def test_dev_credentials_from_env_empty():
    assert dev_credentials_from_env({}) == DevCredentials("", "", "")


def test_load_dev_credentials(monkeypatch):
    monkeypatch.setattr(env_loader, "load_environment", lambda: os.environ.update(_encoded_env()))
    assert env_loader.load_dev_credentials() == DevCredentials("a", "b", "c")
