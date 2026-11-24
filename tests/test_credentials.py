import pytest

from es_ost.core.credentials import DevCredentials, decode_rc_data, encode_rc_data


def test_rc_data_roundtrip():
    encoded = encode_rc_data("mydevid", "p@ss/w0rd", "es-soundtrack-tool")
    assert "mydevid" not in encoded  # values are base64, not plaintext
    assert decode_rc_data(encoded) == DevCredentials("mydevid", "p@ss/w0rd", "es-soundtrack-tool")


def test_rc_data_rejects_invalid_base64():
    with pytest.raises(ValueError):
        decode_rc_data("DEVID=!!!not-base64!!!")


def test_rc_data_empty_template_values():
    assert decode_rc_data("DEVID=\nDEVPASSWORD=\nSOFTNAME=\n") == DevCredentials("", "", "")
