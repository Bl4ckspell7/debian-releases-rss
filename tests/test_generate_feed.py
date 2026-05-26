"""Unit + integration tests for generate_feed."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

import generate_feed as gf


# --- pure-function unit tests ---


def test_load_state_missing(tmp_path: Path) -> None:
    assert gf.load_state(tmp_path / "nope.json") == {}


def test_load_state_empty(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    p.write_text("")
    assert gf.load_state(p) == {}


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "state.json"
    data = {"foo.torrent": {"first_seen": "2026-01-01T00:00:00Z"}}
    gf.save_state(p, data)
    assert gf.load_state(p) == data


def test_cap_history_drops_oldest() -> None:
    state = {f"f{i}.torrent": {"first_seen": f"2026-01-{i:02d}T00:00:00Z"} for i in range(1, 11)}
    capped = gf.cap_history(state, 5)
    assert len(capped) == 5
    # newest 5 kept (days 06..10)
    assert set(capped) == {f"f{i}.torrent" for i in range(6, 11)}


def test_cap_history_noop_under_limit() -> None:
    state = {"a.torrent": {"first_seen": "2026-01-01T00:00:00Z"}}
    assert gf.cap_history(state, 100) == state


def test_first_seen_preserved_across_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    """first_seen must not change when same torrent re-encountered."""
    session = requests.Session()

    def fake_list(source: dict, _session: requests.Session) -> list[str]:
        return ["debian-13.0.0-amd64-netinst.iso.torrent"]

    def fake_meta(_url: str, _session: requests.Session) -> tuple[str, int]:
        return ("2026-05-20T00:00:00Z", 12345)

    monkeypatch.setattr(gf, "list_torrents", fake_list)
    monkeypatch.setattr(gf, "fetch_metadata", fake_meta)

    sources = [{"url": "https://example.com/", "label": "x", "pattern": r".+"}]
    t1 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 1, tzinfo=timezone.utc)

    state = gf.update_state({}, sources, session, t1)
    first_seen_before = state["debian-13.0.0-amd64-netinst.iso.torrent"]["first_seen"]

    state = gf.update_state(state, sources, session, t2)
    first_seen_after = state["debian-13.0.0-amd64-netinst.iso.torrent"]["first_seen"]

    assert first_seen_before == first_seen_after
    assert "2026-05-01" in first_seen_before


def test_build_feed_contains_enclosure_type() -> None:
    state = {
        "debian-13.0.0-amd64-netinst.iso.torrent": {
            "source_url": "https://cdimage.debian.org/debian-cd/current/amd64/bt-cd/",
            "first_seen": "2026-05-01T00:00:00Z",
            "last_modified": "2026-04-30T00:00:00Z",
            "size": 631242752,
        }
    }
    xml = gf.build_feed(state).decode("utf-8")
    assert "application/x-bittorrent" in xml
    assert "debian-13.0.0-amd64-netinst.iso.torrent" in xml
    assert "<enclosure" in xml
    assert "631242752" in xml


def test_build_feed_orders_newest_first() -> None:
    state = {
        "old.torrent": {
            "source_url": "https://example.com/",
            "first_seen": "2026-01-01T00:00:00Z",
            "size": 1,
        },
        "new.torrent": {
            "source_url": "https://example.com/",
            "first_seen": "2026-05-01T00:00:00Z",
            "size": 2,
        },
    }
    xml = gf.build_feed(state).decode("utf-8")
    assert xml.index("new.torrent") < xml.index("old.torrent")


# --- integration: hits the live cdimage server ---


@pytest.mark.integration
def test_live_scrape_each_source_returns_at_least_one() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": gf._user_agent()})
    for source in gf.SOURCES:
        names = gf.list_torrents(source, session)
        assert len(names) >= 1, f"no torrents found at {source['url']}"
        assert all(n.endswith(".torrent") for n in names)
