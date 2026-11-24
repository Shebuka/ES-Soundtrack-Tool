import json
import logging

import pytest

from es_ost.prep import screenscraper
from es_ost.prep.screenscraper import (
    ApiClosedError,
    AuthError,
    BlacklistedError,
    InvalidResponseError,
    NotFoundError,
    Quota,
    QuotaExceededError,
    RateLimitedError,
    ScreenScraperClient,
    ScreenScraperError,
    UnrecognizedLimitError,
    _ERROR_TYPES,
    describe_params,
    parse_api_response,
    query_variants,
)

SUCCESS_BODY = json.dumps(
    {
        "header": {"APIversion": "2.0", "success": "true", "error": ""},
        "response": {"jeux": []},
    }
)
FAILURE_BODY = json.dumps(
    {
        "header": {
            "APIversion": "2.0",
            "success": "false",
            "error": "Service temporarily unavailable",
        }
    }
)


class _FakeResponse:
    """Minimal stand-in for the object returned by ``urlopen``."""

    def __init__(self, body: str) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body.encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


def test_quota_from_ssuser():
    quota = Quota.from_ssuser(
        {
            "maxrequestspermin": "1",
            "maxrequestsperday": "1000",
            "requeststoday": "42",
            "maxthreads": "1",
            "maxdownloadspeed": "50",
        }
    )
    assert quota.maxrequestspermin == 1
    assert quota.requeststoday == 42
    assert quota.maxdownloadspeed == 50


def test_verify_login_throttles_to_the_user_rate_limit(monkeypatch):
    client = ScreenScraperClient("devid", "devpassword", "softname", min_delay=1.0)
    monkeypatch.setattr(
        client,
        "_get",
        lambda endpoint, params: {"response": {"ssuser": {"maxrequestspermin": "6"}}},
    )

    quota = client.verify_login()

    assert quota.maxrequestspermin == 6
    assert client.min_delay == pytest.approx(10.0)


def test_verify_login_speeds_up_a_registered_account(monkeypatch):
    client = ScreenScraperClient("devid", "devpassword", "softname", min_delay=1.0)
    monkeypatch.setattr(
        client,
        "_get",
        lambda endpoint, params: {"response": {"ssuser": {"maxrequestspermin": "600"}}},
    )

    client.verify_login()

    assert client.min_delay == pytest.approx(0.1)


def test_apply_quota_ignores_a_missing_limit():
    client = ScreenScraperClient("devid", "devpassword", "softname", min_delay=1.0)
    client.apply_quota(Quota())

    assert client.min_delay == pytest.approx(1.0)


def test_error_mapping():
    assert _ERROR_TYPES[403] is AuthError
    assert _ERROR_TYPES[429] is RateLimitedError


def test_query_variants_colon():
    """The name as given is tried before the bare prefix."""
    assert query_variants("Earthworm Jim: The Video Game") == [
        "Earthworm Jim - The Video Game",
        "Earthworm Jim: The Video Game",
        "Earthworm Jim",
    ]


def test_fatal_flag_marks_session_level_failures():
    fatal = (
        AuthError,
        BlacklistedError,
        ApiClosedError,
        QuotaExceededError,
        UnrecognizedLimitError,
    )
    assert all(error_type.fatal for error_type in fatal)


def test_fatal_flag_leaves_per_item_failures_skippable():
    skippable = (NotFoundError, RateLimitedError, InvalidResponseError, ScreenScraperError)
    assert not any(error_type.fatal for error_type in skippable)


def test_query_variants_french_spacing():
    assert query_variants("Foo : Bar") == ["Foo - Bar", "Foo : Bar", "Foo"]


def test_query_variants_no_colon():
    assert query_variants("Sonic the Hedgehog") == ["Sonic the Hedgehog"]


def test_query_variants_move_a_leading_article_to_the_end():
    assert query_variants("The Killing Game Show") == [
        "Killing Game Show, The",
        "The Killing Game Show",
    ]


def test_query_variants_offer_the_article_before_the_subtitle():
    """The same game is filed "Sims 3 - Pets, The" and "Sims 3, The - Pets"."""
    # The article-before-dash spelling is more specific than the bare prefix, so
    # it is tried before "The Sims 3".
    assert query_variants("The Sims 3: Pets") == [
        "Sims 3 - Pets, The",
        "Sims 3, The - Pets",
        "The Sims 3: Pets",
        "Sims 3, The",
        "The Sims 3",
    ]


def test_query_variants_tries_standard_form_then_original():
    assert query_variants("Final Fantasy VII") == ["Final Fantasy 7", "Final Fantasy VII"]
    assert query_variants("Rock & Roll Racing") == [
        "Rock And Roll Racing",
        "Rock & Roll Racing",
    ]


def test_query_variants_standardizes_the_prefix_too():
    variants = query_variants("Star Wars: Knights of the Old Republic II")
    assert variants == [
        "Star Wars - Knights of the Old Republic 2",
        "Star Wars: Knights of the Old Republic II",
        "Star Wars",
    ]


def test_search_variants_falls_back_from_the_original_to_the_prefix(monkeypatch):
    client = ScreenScraperClient("devid", "devpassword", "softname")
    calls: list[str] = []

    def fake_search(name, systemeid=None):
        calls.append(name)
        if name == "Earthworm Jim":
            return [{"id": 1}]
        return []

    monkeypatch.setattr(client, "search", fake_search)
    assert client.search_variants("Earthworm Jim: The Video Game") == [{"id": 1}]
    assert calls == [
        "Earthworm Jim - The Video Game",
        "Earthworm Jim: The Video Game",
        "Earthworm Jim",
    ]


def test_search_variants_skips_transient_failure_and_tries_next(monkeypatch):
    client = ScreenScraperClient("devid", "devpassword", "softname")
    calls: list[str] = []

    def fake_search(name, systemeid=None):
        calls.append(name)
        if name == "Earthworm Jim - The Video Game":
            raise InvalidResponseError("<html>503</html>")
        return [{"id": 1}]

    monkeypatch.setattr(client, "search", fake_search)
    assert client.search_variants("Earthworm Jim: The Video Game") == [{"id": 1}]
    assert calls == [
        "Earthworm Jim - The Video Game",
        "Earthworm Jim: The Video Game",
    ]


def test_search_variants_reraises_when_no_variant_matched(monkeypatch):
    client = ScreenScraperClient("devid", "devpassword", "softname")

    def fake_search(name, systemeid=None):
        raise InvalidResponseError("<html>503</html>")

    monkeypatch.setattr(client, "search", fake_search)
    with pytest.raises(InvalidResponseError):
        client.search_variants("Earthworm Jim: The Video Game")


def test_search_variants_does_not_swallow_fatal_error(monkeypatch):
    client = ScreenScraperClient("devid", "devpassword", "softname")
    calls: list[str] = []

    def fake_search(name, systemeid=None):
        calls.append(name)
        raise AuthError("bad credentials", 403)

    monkeypatch.setattr(client, "search", fake_search)
    with pytest.raises(AuthError):
        client.search_variants("Earthworm Jim: The Video Game")
    assert calls == ["Earthworm Jim - The Video Game"]


def test_describe_params_hides_credentials():
    line = describe_params(
        {"gameid": 5441, "ssid": "user", "sspassword": "secret", "devid": "dev"}
    )

    assert "gameid=5441" in line
    assert "secret" not in line
    assert "ssid" not in line
    assert "devid" not in line
    assert describe_params({}) == ""


def test_get_logs_which_item_it_fetches(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(
        screenscraper.urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(SUCCESS_BODY),
    )
    client = _client(tmp_path)

    with caplog.at_level(logging.DEBUG, logger="es_ost.prep.screenscraper"):
        client._get("jeuInfos.php", {"gameid": 5441})

    assert "GET jeuInfos.php gameid=5441 (attempt 1)" in caplog.text
    assert "devpassword" not in caplog.text


def test_search_normalizes_empty_dict_result(monkeypatch):
    client = ScreenScraperClient("devid", "devpassword", "softname")
    monkeypatch.setattr(client, "_get", lambda endpoint, params: {"response": {"jeux": [{}]}})
    assert client.search("No Such Game") == []


def test_parse_api_response_accepts_success_and_bool_success():
    assert parse_api_response(SUCCESS_BODY)["response"] == {"jeux": []}
    assert parse_api_response(json.dumps({"header": {"success": True}})) == {
        "header": {"success": True}
    }


@pytest.mark.parametrize(
    "body",
    [
        "<html><body>500 Internal Server Error</body></html>",
        json.dumps({"header": {"success": "false", "error": "db error"}}),
        json.dumps({"header": {"success": ""}}),
        json.dumps({"response": {"jeux": []}}),
        json.dumps([{"header": {"success": "true"}}]),
    ],
)
def test_parse_api_response_rejects_unusable_bodies(body):
    with pytest.raises(InvalidResponseError):
        parse_api_response(body)


def _client(tmp_path, **kwargs):
    options = {"min_delay": 0.0, "cache_dir": tmp_path, **kwargs}
    return ScreenScraperClient("devid", "devpassword", "softname", **options)


def test_get_retries_failed_header_and_caches_only_success(monkeypatch, tmp_path):
    bodies = [FAILURE_BODY, SUCCESS_BODY]
    calls: list[str] = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        return _FakeResponse(bodies[min(len(calls) - 1, len(bodies) - 1)])

    monkeypatch.setattr(screenscraper.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(screenscraper.time, "sleep", lambda _seconds: None)

    data = _client(tmp_path)._get("jeuRecherche.php", {"recherche": "a-train"})

    assert data["response"] == {"jeux": []}
    assert len(calls) == 2
    cached = list(tmp_path.glob("*.json"))
    assert len(cached) == 1
    assert json.loads(cached[0].read_text(encoding="utf-8"))["header"]["success"] == "true"


def test_get_raises_and_caches_nothing_for_plain_text_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        screenscraper.urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse("<html>Error 503</html>"),
    )
    monkeypatch.setattr(screenscraper.time, "sleep", lambda _seconds: None)

    client = _client(tmp_path, max_retries=1)
    with pytest.raises(InvalidResponseError):
        client._get("jeuInfos.php", {"gameid": 1})
    assert list(tmp_path.glob("*.json")) == []


def test_get_ignores_cached_failure_entry(monkeypatch, tmp_path):
    responses = [SUCCESS_BODY, SUCCESS_BODY]
    calls: list[str] = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        return _FakeResponse(responses[min(len(calls) - 1, len(responses) - 1)])

    monkeypatch.setattr(screenscraper.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(screenscraper.time, "sleep", lambda _seconds: None)

    client = _client(tmp_path)
    client._get("systemesListe.php", {})

    cached = list(tmp_path.glob("*.json"))
    assert len(cached) == 1
    cached[0].write_text(FAILURE_BODY, encoding="utf-8")

    client._get("systemesListe.php", {})
    assert len(calls) == 2
    assert json.loads(cached[0].read_text(encoding="utf-8"))["header"]["success"] == "true"
