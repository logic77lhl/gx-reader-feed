#!/usr/bin/env python3
"""GitHub Actions relay: fetch public X timelines/search via teapawt/Nitter.

The local GX Reader panel only reads feed.json/search.json from this public repo,
so it does not need direct access to Nitter or X. No credentials are used.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
HOSTS = ("teapawt.org", "nitter.gq", "xitter.gq", "xitter.cf", "teapaw.org", "teapawt.net")
ACCOUNTS_DEFAULT = [
    # default / AI / tech
    "OpenAI", "elonmusk", "Reuters", "NASA", "TechCrunch", "BBCWorld",
    "GoogleDeepMind", "SCMPNews", "AnthropicAI", "huggingface", "sama",
    "karpathy", "ylecun", "MistralAI", "xai",
    # news
    "AP", "nytimes", "WSJ", "Bloomberg", "FT", "TheEconomist", "CNN", "AlJazeera",
    # science / China / business / dev / fun
    "SpaceX", "Nature", "ScienceMagazine", "ESA", "CERN",
    "XHNews", "PDChina", "globaltimesnews", "caixin",
    "CNBC", "Forbes", "business",
    "github", "rustlang", "golang", "ThePracticalDev", "freeCodeCamp",
    "dril", "internetarchive", "xkcd", "TheOnion", "verified",
    "shao_han", "Arimuxr", "gugupapa", "ytd_asmr",
    "calm", "Headspace", "ChillhopMusic",
]
QUERY_DEFAULTS = [
    ("AI", "AI"),
    ("OpenAI", "OpenAI"),
    ("technology", "科技"),
    ("news", "新闻"),
    ("opensource", "开源"),
    ("space", "太空"),
    ("photography", "摄影"),
    ("gaming", "游戏"),
    ("ASMR 中文", "ASMR 中文"),
    ("ASMR 音声", "音声"),
    ("jok", "jok"),
    ("north", "north"),
    ("xiangshuijiao", "想睡觉"),
]


def env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


ACCOUNTS = env_list("RELAY_ACCOUNTS", ACCOUNTS_DEFAULT)
_QUERY_LABELS = {query: label for query, label in QUERY_DEFAULTS}
QUERIES = [
    (query, _QUERY_LABELS.get(query, query))
    for query in env_list("RELAY_QUERIES", [query for query, _ in QUERY_DEFAULTS])
]
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
})


def nitter_get(path: str, timeout: int = 25, attempts: int = 3) -> tuple[str, str]:
    errors = []
    for host in HOSTS[:attempts]:
        try:
            r = SESSION.get(f"https://{host}{path}", timeout=timeout, verify=False, allow_redirects=True)
            if r.status_code == 200 and len(r.text) >= 300:
                return r.text, host
            errors.append(f"{host} HTTP {r.status_code}")
        except Exception as exc:
            errors.append(f"{host} {type(exc).__name__}")
    raise RuntimeError("; ".join(errors[-3:]))


def media_url(src: str, host: str) -> str:
    src = (src or "").strip()
    if not src:
        return ""
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return f"https://{host}{src}"
    return src


def upgrade_pbs(url: str) -> str:
    if url and "pbs.twimg.com" in url and "name=" in url:
        return re.sub(r"name=[^&]+", "name=large", url)
    return url


def nitter_date(raw: str) -> str:
    raw = (raw or "").strip()
    for fmt in ("%b %d, %Y · %I:%M %p UTC", "%b %d, %Y · %H:%M UTC"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
    return raw


def stat(item, icon: str):
    el = item.select_one("." + icon)
    if not el:
        return None
    container = el.find_parent(class_="icon-container") or el.parent
    text = container.get_text(" ", strip=True) if container else ""
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else 0


def norm_item(item, host: str, owner: str = ""):
    link = item.select_one("a.tweet-link")
    href = link.get("href", "") if link else ""
    m = re.search(r"/([A-Za-z0-9_]{1,15})/status/(\d+)", href)
    if not m:
        return None
    link_handle, tweet_id = m.group(1), m.group(2)
    username = item.select_one(".username")
    fullname = item.select_one(".fullname")
    screen_name = (username.get_text(strip=True).lstrip("@") if username else link_handle) or link_handle
    name = fullname.get_text(" ", strip=True) if fullname else screen_name
    avatar_el = item.select_one(".tweet-avatar img")
    avatar = media_url(avatar_el.get("src", "") if avatar_el else "", host)
    content = item.select_one(".tweet-content")
    text = content.get_text(" ", strip=True) if content else ""
    date_el = item.select_one(".tweet-date a")
    created_at = nitter_date(date_el.get("title", "") if date_el else "")

    retweet_el = item.select_one(".retweet-header")
    retweeted_by = None
    if retweet_el:
        who = re.sub(r"\s+retweeted$", "", retweet_el.get_text(" ", strip=True), flags=re.I).strip()
        retweeted_by = {"name": who, "screen_name": owner}

    quoted = None
    quote = item.select_one(".quote")
    if quote:
        q_link = quote.select_one("a.quote-link")
        q_href = q_link.get("href", "") if q_link else ""
        qm = re.search(r"/([A-Za-z0-9_]{1,15})/status/(\d+)", q_href)
        q_user = quote.select_one(".username")
        q_name = quote.select_one(".fullname")
        q_text = quote.select_one(".tweet-content")
        q_avatar = quote.select_one(".tweet-avatar img") or quote.select_one("img.avatar")
        q_handle = (q_user.get_text(strip=True).lstrip("@") if q_user else (qm.group(1) if qm else ""))
        quoted = {
            "id": qm.group(2) if qm else "",
            "url": f"https://x.com/{q_handle}/status/{qm.group(2)}" if qm else "",
            "text": q_text.get_text(" ", strip=True) if q_text else "",
            "author": {
                "name": q_name.get_text(" ", strip=True) if q_name else q_handle,
                "screen_name": q_handle,
                "avatar": media_url(q_avatar.get("src", "") if q_avatar else "", host),
                "verified": bool(quote.select_one(".verified-icon")),
            },
        }

    photos = []
    for img in item.select(".attachments .still-image img, .attachments .tweet-media img, .tweet-media img"):
        src = media_url(img.get("src") or img.get("data-src") or "", host)
        if src and src not in photos:
            photos.append(upgrade_pbs(src))

    videos = []
    for vid in item.select("video"):
        source = vid.select_one("source[src]")
        vurl = media_url(source.get("src", "") if source else "", host)
        poster = media_url(vid.get("poster", ""), host)
        if vurl or poster:
            videos.append({"url": vurl, "thumbnail": upgrade_pbs(poster), "type": "video"})

    return {
        "id": tweet_id,
        "url": f"https://x.com/{screen_name}/status/{tweet_id}",
        "text": text,
        "created_at": created_at,
        "lang": "",
        "author": {
            "name": name,
            "screen_name": screen_name,
            "avatar": avatar,
            "verified": bool(item.select_one(".verified-icon")),
            "followers": None,
            "description": "",
        },
        "stats": {
            "likes": stat(item, "icon-heart"),
            "retweets": stat(item, "icon-retweet"),
            "replies": stat(item, "icon-comment"),
            "quotes": stat(item, "icon-quote"),
            "views": stat(item, "icon-views"),
        },
        "photos": photos,
        "videos": videos,
        "card_image": "",
        "quoted": quoted,
        "is_retweet": bool(retweeted_by),
        "retweeted_by": retweeted_by,
        "possibly_sensitive": bool(item.select_one(".sensitive")),
        "source": "nitter",
        "via": f"Nitter · {host}",
    }


def parse_html(html: str, host: str, limit: int = 20, owner: str = ""):
    soup = BeautifulSoup(html, "html.parser")
    items = soup.select(".timeline-item")
    if not items:
        raise RuntimeError("no timeline items")

    user = None
    profile_handle = soup.select_one(".profile-card-username")
    if profile_handle:
        handle = profile_handle.get_text(strip=True).lstrip("@")
        profile_name = soup.select_one(".profile-card-fullname")
        profile_avatar = soup.select_one(".profile-card-avatar img")
        profile_bio = soup.select_one(".profile-bio")
        followers_el = soup.select_one(".profile-card-extra .profile-stat-num")
        followers = None
        if followers_el:
            digits = re.sub(r"[^\d]", "", followers_el.get_text(" ", strip=True))
            followers = int(digits) if digits else None
        user = {
            "name": profile_name.get_text(" ", strip=True) if profile_name else handle,
            "screen_name": handle,
            "avatar": media_url(profile_avatar.get("src", "") if profile_avatar else "", host),
            "verified": bool(soup.select_one(".profile-card .verified-icon")),
            "followers": followers,
            "description": profile_bio.get_text(" ", strip=True) if profile_bio else "",
        }

    tweets = []
    seen = set()
    for item in items:
        tweet = norm_item(item, host, owner)
        if not tweet or not tweet.get("id") or tweet["id"] in seen:
            continue
        if not tweet.get("text") and not tweet.get("photos") and not tweet.get("videos"):
            continue
        seen.add(tweet["id"])
        tweets.append(tweet)
        if user is None:
            user = tweet["author"]
        if len(tweets) >= limit:
            break
    if not tweets:
        raise RuntimeError("no tweets")
    return user, tweets


def fetch_timeline(handle: str):
    html, host = nitter_get(f"/{quote(handle)}")
    user, tweets = parse_html(html, host, 20, handle)
    return {"user": user, "tweets": tweets, "via": f"Nitter · {host}"}


def fetch_search(query: str):
    html, host = nitter_get(f"/search?f=tweets&q={quote(query)}")
    _, tweets = parse_html(html, host, 20)
    return {"tweets": tweets, "via": f"Nitter · {host}"}


def tweet_ts(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def main():
    now = datetime.now(timezone.utc).isoformat()
    accounts = []
    all_tweets = []
    errors = []

    for handle in ACCOUNTS:
        try:
            data = fetch_timeline(handle)
            time.sleep(0.15)
            user = data["user"]
            accounts.append({
                "screen_name": user.get("screen_name") or handle,
                "name": user.get("name") or handle,
                "avatar": user.get("avatar") or "",
                "verified": bool(user.get("verified")),
                "followers": user.get("followers"),
                "description": user.get("description") or "",
                "count": len(data["tweets"]),
                "error": "",
            })
            all_tweets.extend(data["tweets"])
        except Exception as exc:
            errors.append({"account": handle, "code": "NITTER_FAILED", "message": str(exc)[:180]})
            accounts.append({"screen_name": handle, "name": handle, "avatar": "", "verified": False, "error": str(exc)[:180]})

    seen = set()
    tweets = []
    for tweet in all_tweets:
        if tweet["id"] in seen:
            continue
        seen.add(tweet["id"])
        tweets.append(tweet)
    tweets.sort(key=lambda t: tweet_ts(t.get("created_at", "")), reverse=True)

    feed = {
        "ok": bool(tweets),
        "updated_at": now,
        "via": "GitHub Actions · Nitter 公共实例",
        "accounts": accounts,
        "tweets": tweets,
        "count": len(tweets),
        "errors": errors,
    }
    (ROOT / "feed.json").write_text(json.dumps(feed, ensure_ascii=False, indent=1), encoding="utf-8")

    searches = {}
    search_errors = []
    for query, label in QUERIES:
        try:
            data = fetch_search(query)
            time.sleep(0.15)
            searches[query] = {
                "label": label,
                "query": query,
                "tweets": data["tweets"],
                "count": len(data["tweets"]),
                "via": data["via"],
                "errors": [],
            }
        except Exception as exc:
            search_errors.append({"query": query, "message": str(exc)[:180]})
            searches[query] = {
                "label": label,
                "query": query,
                "tweets": [],
                "count": 0,
                "via": "",
                "errors": [{"code": "NITTER_FAILED", "message": str(exc)[:180]}],
            }

    search_payload = {
        "ok": any(v["count"] for v in searches.values()),
        "updated_at": now,
        "via": "GitHub Actions · Nitter 公共实例",
        "queries": searches,
        "errors": search_errors,
    }
    (ROOT / "search.json").write_text(json.dumps(search_payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({
        "feed_count": len(tweets),
        "feed_errors": len(errors),
        "search_counts": {k: v["count"] for k, v in searches.items()},
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
