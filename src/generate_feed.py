"""Scrape cdimage.debian.org for .torrent files and emit an RSS feed."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

SOURCES: list[dict[str, str]] = [
    {
        "url": "https://cdimage.debian.org/debian-cd/current/amd64/bt-cd/",
        "label": "netinst",
        "pattern": r".+\.torrent$",
    },
    {
        "url": "https://cdimage.debian.org/debian-cd/current-live/amd64/bt-hybrid/",
        "label": "live",
        "pattern": r".+\.torrent$",
    },
]

MAX_ENTRIES: int = 100
REQUEST_TIMEOUT: int = 15

FEED_TITLE: str = "Debian Release Torrents"
FEED_DESCRIPTION: str = "Debian ISO torrent releases from cdimage.debian.org"

ROOT: Path = Path(__file__).resolve().parent.parent
STATE_PATH: Path = ROOT / "src" / "state.json"
OUTPUT_PATH: Path = ROOT / "output" / "feed.xml"


def _github_repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY", "USER/debian-releases-rss")


def _user_agent() -> str:
    return f"debian-releases-rss/0.1 (+https://github.com/{_github_repo()})"


def _feed_link() -> str:
    user, repo = _github_repo().split("/", 1)
    return f"https://{user}.github.io/{repo}/feed.xml"


def load_state(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    return json.loads(raw)


def save_state(path: Path, state: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def list_torrents(source: dict[str, str], session: requests.Session) -> list[str]:
    """Return torrent filenames matching the source's pattern."""
    resp = session.get(source["url"], timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    regex = re.compile(source["pattern"])
    names: list[str] = []
    for a in soup.find_all("a"):
        href = a.get("href", "")
        if "/" in href or href.startswith("?"):
            continue
        if regex.match(href):
            names.append(href)
    return names


def fetch_metadata(url: str, session: requests.Session) -> tuple[str | None, int | None]:
    """HEAD the torrent URL → (last_modified ISO-8601 UTC, size_bytes)."""
    try:
        resp = session.head(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException:
        return None, None
    last_modified: str | None = None
    if "Last-Modified" in resp.headers:
        try:
            dt = parsedate_to_datetime(resp.headers["Last-Modified"]).astimezone(timezone.utc)
            last_modified = dt.isoformat().replace("+00:00", "Z")
        except TypeError, ValueError:
            last_modified = None
    size: int | None = None
    if "Content-Length" in resp.headers:
        try:
            size = int(resp.headers["Content-Length"])
        except ValueError:
            size = None
    return last_modified, size


def update_state(
    state: dict[str, dict],
    sources: list[dict[str, str]],
    session: requests.Session,
    now: datetime,
) -> dict[str, dict]:
    """Walk sources, add new torrents to state, refresh metadata on known ones."""
    now_iso = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    for source in sources:
        try:
            names = list_torrents(source, session)
        except requests.RequestException as e:
            print(f"  WARN: failed to list {source['url']}: {e}", file=sys.stderr)
            continue
        for name in names:
            full_url = urljoin(source["url"], name)
            entry = state.get(name, {})
            need_meta = "size" not in entry or "last_modified" not in entry
            if need_meta:
                last_modified, size = fetch_metadata(full_url, session)
                if last_modified is not None:
                    entry["last_modified"] = last_modified
                if size is not None:
                    entry["size"] = size
            if "first_seen" not in entry:
                entry["first_seen"] = now_iso
            entry["source_url"] = source["url"]
            state[name] = entry
    return state


def cap_history(state: dict[str, dict], max_entries: int) -> dict[str, dict]:
    if len(state) <= max_entries:
        return state
    sorted_items = sorted(state.items(), key=lambda kv: kv[1].get("first_seen", ""), reverse=True)
    return dict(sorted_items[:max_entries])


def build_feed(state: dict[str, dict]) -> bytes:
    fg = FeedGenerator()
    fg.title(FEED_TITLE)
    fg.link(href=_feed_link(), rel="self")
    fg.link(href=f"https://github.com/{_github_repo()}", rel="alternate")
    fg.description(FEED_DESCRIPTION)
    fg.language("en")

    sorted_entries = sorted(state.items(), key=lambda kv: kv[1].get("first_seen", ""))
    for name, meta in sorted_entries:
        full_url = urljoin(meta["source_url"], name)
        fe = fg.add_entry()
        fe.title(name)
        fe.guid(name, permalink=False)
        fe.link(href=full_url)
        fe.description(f"Torrent: {full_url}")
        fe.pubDate(meta["first_seen"])
        size = meta.get("size")
        fe.enclosure(
            url=full_url,
            length=str(size) if isinstance(size, int) and size > 0 else "",
            type="application/x-bittorrent",
        )
    return fg.rss_str(pretty=True)


def main() -> int:
    session = requests.Session()
    session.headers.update({"User-Agent": _user_agent()})

    state = load_state(STATE_PATH)
    print(f"Loaded {len(state)} entries from state")

    state = update_state(state, SOURCES, session, datetime.now(timezone.utc))
    print(f"After scrape: {len(state)} entries")

    state = cap_history(state, MAX_ENTRIES)
    print(f"After cap (max={MAX_ENTRIES}): {len(state)} entries")

    # Persist state BEFORE writing xml. If xml write fails after a successful
    # state write, the next run still treats torrents as already-seen and
    # preserves first_seen → qBittorrent will not re-download.
    save_state(STATE_PATH, state)

    xml_bytes = build_feed(state)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(xml_bytes)

    print(f"Wrote {OUTPUT_PATH} ({len(xml_bytes)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
