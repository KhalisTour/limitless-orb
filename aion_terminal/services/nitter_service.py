"""
Nitter RSS fetcher — no API key, uses public instances.
Fetches timelines for 5 watched X accounts.
"""
from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
]

WATCHED_ACCOUNTS = [
    "FirstSquawk",
    "DeItaone",
    "Fl0wG0d",
    "TrendSpider",
    "Labeltrader1122",
]


def fetch_account_feed(username: str, instance: str, timeout: int = 8) -> list[dict]:
    url = f"{instance}/{username}/rss"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        root = ET.fromstring(content)
        items = []
        for item in root.findall(".//item")[:10]:
            title = item.findtext("title") or ""
            desc = item.findtext("description") or ""
            link = item.findtext("link") or ""
            pub = item.findtext("pubDate") or ""
            text = re.sub(r"<[^>]+>", "", desc or title).strip()
            tickers = re.findall(r"\$([A-Z]{1,5})\b", text)
            tickers += re.findall(
                r"\b([A-Z]{2,5})\b(?=\s+(?:calls?|puts?|sweep|flow|unusual))",
                text,
            )
            items.append(
                {
                    "username": username,
                    "text": text[:280],
                    "url": link,
                    "published_at": pub,
                    "mentions_tickers": list(set(tickers)),
                }
            )
        return items
    except Exception:
        return []


def fetch_all_feeds(watchlist: list[str]) -> dict:
    watchlist_upper = [s.upper() for s in watchlist]
    all_posts: list[dict] = []
    instance_used: str | None = None

    for account in WATCHED_ACCOUNTS:
        posts: list[dict] = []
        for instance in NITTER_INSTANCES:
            posts = fetch_account_feed(account, instance)
            if posts:
                instance_used = instance
                break
        for p in posts:
            p["watchlist_match"] = [
                t for t in p["mentions_tickers"] if t in watchlist_upper
            ]
        all_posts.extend(posts)

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "posts": all_posts,
        "accounts_fetched": len(WATCHED_ACCOUNTS),
        "instance_used": instance_used or "none",
    }
