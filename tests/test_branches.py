"""Branches the fixture API tests never take: .env quirks, live openFDA, bad dates."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import openfda
from app.config import load_dotenv, settings
from app.main import app
from app.models import Update

client = TestClient(app)


def test_load_dotenv(tmp_path: Path, monkeypatch):
    import os

    load_dotenv(tmp_path / "missing.env")
    env = tmp_path / ".env"
    env.write_text("\n# comment\nskip-me\nNEW=from-file\nKEEP=from-file\n", encoding="utf-8")
    monkeypatch.setenv("KEEP", "from-environment")
    monkeypatch.delenv("NEW", raising=False)
    load_dotenv(env)
    monkeypatch.setenv("NEW", os.environ["NEW"])
    assert os.environ["NEW"] == "from-file"
    assert os.environ["KEEP"] == "from-environment"


def test_unreadable_dates_are_dropped_or_error():
    bad = Update(
        id="x", title="t", date="nope", jurisdiction="FDA", tags=[], source_url="u", body="b"
    )
    with patch("app.main.portfolio.load_updates", return_value=[bad]):
        assert client.get("/updates", params={"since": "2025-01-01"}).status_code == 500
    with patch("app.main.openfda.search_recalls", return_value=[{"event_date_initiated": "bad"}]):
        assert client.get("/recalls", params={"since": "2020-01-01"}).json() == []


def test_live_openfda_query_errors_and_empty_results():
    live = replace(settings, openfda_mode="live", openfda_api_key="k")
    ok = MagicMock(status_code=200)
    ok.json.return_value = {"results": [{"k": 1}]}
    with patch.object(openfda, "settings", live):
        with patch("app.openfda.httpx.get", return_value=ok) as get:
            assert openfda.search_510k("a b") == [{"k": 1}]
            params = get.call_args.kwargs["params"]
            assert params["search"] == "device_name:a AND device_name:b"
            assert params["api_key"] == "k"

    blank = replace(settings, openfda_mode="live", openfda_api_key="")
    empty = MagicMock(status_code=200)
    empty.json.return_value = {}
    with patch.object(openfda, "settings", blank):
        with patch("app.openfda.httpx.get", return_value=empty) as get:
            assert openfda.search_recalls("") == []
            assert "search" not in get.call_args.kwargs["params"]

        for status, outcome in ((404, []), (403, 502), (500, 502)):
            resp = MagicMock(status_code=status)
            with patch("app.openfda.httpx.get", return_value=resp):
                if outcome == []:
                    assert openfda.search_510k("x") == []
                else:
                    with pytest.raises(HTTPException) as exc:
                        openfda.search_510k("x")
                    assert exc.value.status_code == outcome

        with patch("app.openfda.httpx.get", side_effect=httpx.TimeoutException("t")):
            with pytest.raises(HTTPException) as exc:
                openfda.search_510k("x")
            assert exc.value.status_code == 504
        with patch("app.openfda.httpx.get", side_effect=httpx.ConnectError("n")):
            with pytest.raises(HTTPException) as exc:
                openfda.search_510k("x")
            assert exc.value.status_code == 502
