#!/usr/bin/env python3
"""
build_ai_news_weekly.py — 周维度聚合 AI 行业资讯

两阶段：
  Stage 1 (本脚本独立完成): 读过去 7 天的 raw snapshot，合并去重，
    生成供 LLM 消费的 aggregated.json（只做数据加工，不做内容判断）
  Stage 2 (由 Claude Code / 其它 LLM 执行): 读 aggregated.json + prompt_brief.md
    产出最终周报 data/ai-news/weekly/YYYY-Www.json

用法:
    python3 build_ai_news_weekly.py              # 当前周
    python3 build_ai_news_weekly.py 2026-W19     # 指定周（ISO周）

产物:
    data/ai-news/weekly/YYYY-Www.raw.json      — 聚合原料（供 LLM 读）
    data/ai-news/weekly/YYYY-Www.brief.md      — 给 LLM 的指令+角色设定
    data/ai-news/weekly/YYYY-Www.json          — 最终周报（由 LLM 生成后填入）
"""

from __future__ import annotations

import json
import sys
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "ai-news" / "raw"
WEEKLY_DIR = ROOT / "data" / "ai-news" / "weekly"


# ---- 周工具 ----

def iso_week_key(d: date) -> str:
    """date → '2026-W19'"""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_range(week_key: str) -> tuple[date, date]:
    """'2026-W19' → (周一date, 周日date)"""
    year_str, wk_str = week_key.split("-W")
    year = int(year_str)
    wk = int(wk_str)
    # ISO周一
    monday = date.fromisocalendar(year, wk, 1)
    sunday = monday + timedelta(days=6)
    return monday, sunday


# ---- 聚合 ----

def load_raw_for_week(monday: date, sunday: date) -> list[dict]:
    """读本周 7 天内所有 raw snapshot"""
    snapshots = []
    d = monday
    while d <= sunday:
        fp = RAW_DIR / f"{d.isoformat()}.json"
        if fp.exists():
            try:
                with fp.open("r", encoding="utf-8") as f:
                    snapshots.append(json.load(f))
            except Exception as e:
                print(f"  ! skip {fp.name}: {e}", file=sys.stderr)
        d += timedelta(days=1)
    return snapshots


def aggregate(snapshots: list[dict]) -> dict:
    """合并 7 天 snapshot，按 ID 去重"""
    tweets_by_id: "OrderedDict[str, dict]" = OrderedDict()
    builders: dict[str, dict] = {}  # handle -> bio/name
    podcasts_by_guid: "OrderedDict[str, dict]" = OrderedDict()
    blogs_by_url: "OrderedDict[str, dict]" = OrderedDict()

    for snap in snapshots:
        # X / Twitter
        for b in (snap.get("x") or {}).get("x", []) or []:
            handle = b.get("handle", "")
            if handle and handle not in builders:
                builders[handle] = {
                    "name": b.get("name", ""),
                    "handle": handle,
                    "bio": b.get("bio", ""),
                }
            for t in b.get("tweets", []) or []:
                tid = t.get("id")
                if not tid or tid in tweets_by_id:
                    continue
                t2 = dict(t)
                t2["_handle"] = handle
                t2["_builderName"] = b.get("name", "")
                tweets_by_id[tid] = t2

        # Podcasts
        for p in (snap.get("podcasts") or {}).get("podcasts", []) or []:
            guid = p.get("guid") or p.get("url")
            if not guid or guid in podcasts_by_guid:
                continue
            podcasts_by_guid[guid] = p

        # Blogs
        for post in (snap.get("blogs") or {}).get("blogs", []) or []:
            url = post.get("url")
            if not url or url in blogs_by_url:
                continue
            blogs_by_url[url] = post

    # 排序：推文按发布时间降序，便于LLM阅读
    tweets_sorted = sorted(
        tweets_by_id.values(),
        key=lambda t: t.get("createdAt") or "",
        reverse=True,
    )

    return {
        "tweets": tweets_sorted,
        "builders": list(builders.values()),
        "podcasts": list(podcasts_by_guid.values()),
        "blogs": list(blogs_by_url.values()),
        "stats": {
            "tweets": len(tweets_sorted),
            "builders": len(builders),
            "podcasts": len(podcasts_by_guid),
            "blogs": len(blogs_by_url),
            "rawSnapshots": len(snapshots),
        },
    }


# ---- LLM 指令 ----

PROMPT_BRIEF_TEMPLATE = """# 生成本周 AI 行业资讯周报

## 你的角色
你在给一个产品经理（小圆子）做「一周 AI 行业快报」。他是腾讯搜狗输入法 PM，
负责输入法侧边栏 WorkBuddy/牛牛 AI 助手，关注点聚焦在：AI 产品形态、
交互范式、设计趋势、大模型能力边界、商业化方向。
他的风格偏好：**大白话、禁止拗口造词、说人话**，反感生造英文和营销腔。

## 输入数据
从 `{raw_file}` 读过去 7 天（{monday} ~ {sunday}）的原始数据：
- tweets: {n_tweets} 条 AI 大佬推文（附 builder 信息）
- podcasts: {n_podcasts} 期顶级 AI 播客（含完整转写）
- blogs: {n_blogs} 篇 Anthropic / Claude 官方博客

## 产出要求
写一个 JSON 文件到 `{output_file}`，严格遵守以下结构：

```json
{{
  "week": "{week_key}",
  "range": {{ "start": "{monday}", "end": "{sunday}" }},
  "generatedAt": "ISO 时间戳",
  "trends": [
    {{
      "title": "趋势标题（不超过12字，大白话）",
      "summary": "2-3句话说清楚这是啥趋势、为什么值得关注",
      "implication": "对输入法/AI助手类产品的启发（1-2句）",
      "evidence": [
        {{ "type": "tweet|podcast|blog", "who": "谁说的/出自哪", "url": "原文链接", "quote": "关键一句引用（中文翻译后的）" }}
      ]
    }}
  ],
  "builders": [
    {{
      "name": "大佬名字",
      "handle": "twitter handle",
      "role": "从 bio 提炼的一句话角色（如 Box CEO、OpenAI 联创）",
      "highlights": [
        {{ "summary": "这条推的核心观点（1-2句中文）", "url": "推文链接" }}
      ]
    }}
  ],
  "podcasts": [
    {{
      "name": "播客名",
      "title": "剧集标题（中文翻译+原文）",
      "guest": "嘉宾（如果有）",
      "url": "原文链接",
      "keyTakeaways": ["3-5条核心观点，中文，每条1-2句"]
    }}
  ],
  "blogs": [
    {{
      "name": "博客来源",
      "title": "文章标题（中文+原文）",
      "url": "原文链接",
      "summary": "3-5句中文摘要，讲清楚讲了啥+为啥重要"
    }}
  ]
}}
```

## 硬性规则
1. **trends 必须是 3 条**——不多不少。AI 行业这周真的没啥值得关注的时候，也要挑 3 个最不无聊的主题。
2. **每条 trend 至少带 2 条 evidence**，都要有真实 URL，URL 必须来自输入数据，**不许编造**。
3. **builders 只保留有"有价值观点"的人**——纯宣传、纯转发、纯段子、纯广告不要。宁缺毋滥。
4. **语言口语化**——小圆子反感"赋能/抓手/闭环/飞轮/L1-L4"这种词。用"这说明""有意思的是""值得留意"这种自然表达。
5. **翻译要意译不要直译**，保留原作者的语气和立场。
6. **引用原文时保留英文双语**：给中文翻译为主，英文短句原文附在括号里（仅 trends.evidence.quote 里保留原文）。
7. **不要写"总结""综上所述""结语"这种套话**。写完最后一个 blog 就 JSON 结尾。
8. **严格 JSON**——别写 markdown 代码块，直接输出纯 JSON 到文件。
"""


def build_brief(week_key: str, monday: date, sunday: date, agg: dict,
                raw_file: Path, output_file: Path) -> str:
    return PROMPT_BRIEF_TEMPLATE.format(
        week_key=week_key,
        monday=monday.isoformat(),
        sunday=sunday.isoformat(),
        n_tweets=agg["stats"]["tweets"],
        n_podcasts=agg["stats"]["podcasts"],
        n_blogs=agg["stats"]["blogs"],
        raw_file=raw_file.relative_to(ROOT),
        output_file=output_file.relative_to(ROOT),
    )


# ---- 主流程 ----

def main() -> int:
    if len(sys.argv) > 1:
        week_key = sys.argv[1]
    else:
        week_key = iso_week_key(date.today())

    monday, sunday = week_range(week_key)
    print(f"→ Building AI news weekly for {week_key} ({monday} ~ {sunday})")

    WEEKLY_DIR.mkdir(parents=True, exist_ok=True)

    snapshots = load_raw_for_week(monday, sunday)
    print(f"  · loaded {len(snapshots)} daily snapshots from raw/")

    if not snapshots:
        print("  ! No raw data found for this week. Run fetch_ai_news_daily.py first.",
              file=sys.stderr)
        return 1

    agg = aggregate(snapshots)
    print(f"  · aggregated: {agg['stats']}")

    raw_file = WEEKLY_DIR / f"{week_key}.raw.json"
    brief_file = WEEKLY_DIR / f"{week_key}.brief.md"
    output_file = WEEKLY_DIR / f"{week_key}.json"

    agg_out = {
        "week": week_key,
        "range": {"start": monday.isoformat(), "end": sunday.isoformat()},
        "builtAt": datetime.now(timezone.utc).isoformat(),
        **agg,
    }
    with raw_file.open("w", encoding="utf-8") as f:
        json.dump(agg_out, f, ensure_ascii=False, indent=2)

    brief = build_brief(week_key, monday, sunday, agg, raw_file, output_file)
    with brief_file.open("w", encoding="utf-8") as f:
        f.write(brief)

    print(f"✓ {raw_file.relative_to(ROOT)} ({raw_file.stat().st_size/1024:.1f} KB)")
    print(f"✓ {brief_file.relative_to(ROOT)}")

    # 更新周索引（供前端下拉切换）
    update_weekly_index()
    print()
    print("下一步：让 Claude Code 读 brief.md 和 raw.json，生成最终周报 JSON。")
    print(f"  提示词：'按 {brief_file.relative_to(ROOT)} 的要求，生成本周AI资讯周报JSON'")
    return 0


def update_weekly_index() -> None:
    """扫 weekly/*.json 生成 index.json 供前端下拉切换使用"""
    entries = []
    for fp in sorted(WEEKLY_DIR.glob("*.json")):
        name = fp.stem  # 例如 2026-W19 或 2026-W19.raw
        if name == "index":
            continue
        if name.endswith(".raw") or name.endswith(".brief"):
            continue
        try:
            with fp.open("r", encoding="utf-8") as f:
                data = json.load(f)
            week_key = data.get("week") or name
            rng = data.get("range") or {}
            entries.append({
                "week": week_key,
                "start": rng.get("start"),
                "end": rng.get("end"),
                "generatedAt": data.get("generatedAt"),
                "file": fp.name,
            })
        except Exception:
            continue

    # 倒序：最新的周在前
    entries.sort(key=lambda e: e["week"], reverse=True)
    index = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(entries),
        "weeks": entries,
    }
    index_path = WEEKLY_DIR / "index.json"
    with index_path.open("w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"✓ {index_path.relative_to(ROOT)} ({len(entries)} weeks)")


if __name__ == "__main__":
    sys.exit(main())
