#!/usr/bin/env python3
"""
fetch_ai_news_daily.py — 每日采集 AI 行业资讯原始数据

思路：直接从 follow-builders 作者 GitHub 仓库拉 3 个 feed JSON：
  - feed-x.json      : 25 位 AI builder 的近 24 小时推文
  - feed-podcasts.json : 6 个顶级 AI 播客的近 14 天新剧集（含全文转写）
  - feed-blogs.json  : Anthropic / Claude 博客近 72 小时新文章

每天跑一次存到 data/ai-news/raw/YYYY-MM-DD.json，作为周聚合的原料。
作者端每日 6am UTC 刷新 feed，用 state-feed.json 做全局去重，不会重复。

用法:
    python3 fetch_ai_news_daily.py              # 保存今天
    python3 fetch_ai_news_daily.py 2026-05-06   # 保存指定日期
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://raw.githubusercontent.com/zarazhangrui/follow-builders/main"
FEEDS = {
    "x": f"{BASE}/feed-x.json",
    "podcasts": f"{BASE}/feed-podcasts.json",
    "blogs": f"{BASE}/feed-blogs.json",
}

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "ai-news" / "raw"


def fetch_json(url: str, timeout: int = 30) -> dict | None:
    """拉一个 JSON，失败返回 None（不中断其他 feed 的采集）"""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ai-insights-hub/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"  ! fetch failed {url}: {exc}", file=sys.stderr)
        return None


def main() -> int:
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now(
        timezone.utc).astimezone().strftime("%Y-%m-%d")

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DIR / f"{date_str}.json"

    print(f"→ Fetching AI news feeds for {date_str}")
    snapshot = {
        "date": date_str,
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "source": "follow-builders feeds",
        "x": None,
        "podcasts": None,
        "blogs": None,
    }

    for key, url in FEEDS.items():
        print(f"  · {key} …", end=" ", flush=True)
        data = fetch_json(url)
        snapshot[key] = data
        if data is None:
            print("FAIL")
            continue
        # 打印概要
        if key == "x":
            n_builders = len(data.get("x", []) or [])
            n_tweets = sum(len(b.get("tweets", []) or [])
                           for b in data.get("x", []) or [])
            print(f"ok ({n_builders} builders, {n_tweets} tweets)")
        elif key == "podcasts":
            n = len(data.get("podcasts", []) or [])
            print(f"ok ({n} episodes)")
        elif key == "blogs":
            n = len(data.get("blogs", []) or [])
            print(f"ok ({n} posts)")

    with out_path.open("w", encoding="utf-8") as fp:
        json.dump(snapshot, fp, ensure_ascii=False, indent=2)

    size_kb = out_path.stat().st_size / 1024
    print(f"✓ Saved → {out_path.relative_to(ROOT)} ({size_kb:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
