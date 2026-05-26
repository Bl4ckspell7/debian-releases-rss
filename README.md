# debian-releases-rss

Daily-updated RSS feed of Debian ISO torrents from cdimage.debian.org, hosted on GitHub Pages for qBittorrent auto-download.

Debian doesn't publish an official RSS feed for releases (unlike Arch Linux). This repo scrapes [cdimage.debian.org](https://cdimage.debian.org) once a day via GitHub Actions, builds an RSS XML with `<enclosure type="application/x-bittorrent">` items, and publishes it to GitHub Pages.

## Feed URL

```
https://bl4ckspell7.github.io/debian-releases-rss/feed.xml
```

Includes every `.torrent` from:

- `current/amd64/bt-cd/` (netinst)
- `current-live/amd64/bt-hybrid/` (live images: gnome, kde, xfce, …)

Filter what you actually want inside qBittorrent's RSS download rules.

## qBittorrent setup

1. Tools → Options → RSS → enable RSS, enable auto-downloader.
2. View → RSS Reader → New subscription → paste the feed URL.
3. (Optional) RSS Downloader → New rule, e.g. `Must contain: amd64-netinst`, set a save path.

## Local development

```bash
uv sync
uv run src/generate_feed.py
```

Outputs `output/feed.xml` and updates `src/state.json`.

Run tests:

```bash
uv run pytest                # unit tests
uv run pytest -m integration # live HTTP scrape
uv run ruff check
uv run ruff format --check
```

## One-time GitHub setup

Settings → Pages → Source = **GitHub Actions**.

Then trigger `Update Feed` workflow manually once to seed the feed.

## How state works

`src/state.json` is committed back to `main` each run. It tracks `first_seen` per torrent filename so `<pubDate>` stays stable across runs — qBittorrent won't re-download the same torrent on the next cron tick. History caps at 100 entries (oldest dropped by `first_seen`).

## License

GPL-3.0. See [LICENSE](LICENSE).
