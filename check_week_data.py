#!/usr/bin/env python3
"""
check_week_data.py — AI Insights Hub 周数据完整性门禁

背景：2026-08-17 主流程自动化被取消（499 canceled），步骤 1/2 未执行完，
但下游链路（企微周报、资讯提交）照常运行，页面降级为兜底数据显示 3/6/1，
失败被伪装成了成功。本脚本用于把「静默降级」变成「显式失败」。

校验内容：
  1) 指定周（周一起 7 天）的 data/YYYY-MM-DD.json 是否全部存在且可解析
  2) data/design-weekly-YYYY-MM-DD.json 是否存在
     注意命名约定：周报文件名日期 = 生成日（内容周结束后的那个周一），
     即校验 2026-08-10~08-16 这一周时，对应周报是 design-weekly-2026-08-17.json
  3) 日报字段完整性与条目数区间（含 demand_mining < design_insights 规则）
  4) 周报 5 个 stages 各 3 条，字段齐全

用法:
    python3 check_week_data.py                # 校验「上一个完整周」
    python3 check_week_data.py 2026-08-10     # 校验指定周（须为周一）
    python3 check_week_data.py --this-week    # 校验本周（周一起）

退出码:
    0 = 全部通过，下游链路可继续
    1 = 存在缺失或不合规，调用方必须中止后续动作（提交、发送、声称成功）
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"

# 日报字段规范
DAILY_TOP = ["date", "generated_at", "daily_summary",
             "design_insights", "demand_mining", "hot_news"]
DI_FIELDS = {"title", "summary", "implication",
             "sources", "source_urls", "tags"}
DM_FIELDS = {"title", "pain_point", "opportunity", "priority", "evidence"}
HN_FIELDS = {"title", "summary", "source", "source_urls", "tags"}

# 条目数区间（与 validate.py 保持一致）
DI_RANGE = (3, 6)
DM_RANGE = (2, 4)
HN_RANGE = (8, 15)

# 周报字段规范
STAGES = ["research", "competitive", "user_research",
          "design_output", "design_validation"]
STAGE_ITEM_FIELDS = {"title", "source", "sourceDetail",
                     "insight", "action", "tags", "sourceUrls"}
STAGE_ITEM_COUNT = 3

errors: list[str] = []
warns: list[str] = []


def err(msg: str) -> None:
    errors.append(msg)


def warn(msg: str) -> None:
    warns.append(msg)


def last_monday(today: date | None = None) -> date:
    """返回上一个完整周的周一。周一当天跑 → 上周一。"""
    today = today or date.today()
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(days=7)


def this_monday(today: date | None = None) -> date:
    today = today or date.today()
    return today - timedelta(days=today.weekday())


def check_daily(day: date) -> None:
    ds = day.strftime("%Y-%m-%d")
    path = DATA / f"{ds}.json"
    if not path.exists():
        err(f"[缺失] 日报文件不存在: data/{ds}.json")
        return
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        err(f"[损坏] data/{ds}.json 无法解析: {exc}")
        return

    for k in DAILY_TOP:
        if k not in d:
            err(f"[字段] data/{ds}.json 缺顶层字段 {k}")
    if d.get("date") != ds:
        err(f"[字段] data/{ds}.json 的 date={d.get('date')} 与文件名不一致")
    if not (d.get("daily_summary") or "").strip():
        err(f"[内容] data/{ds}.json daily_summary 为空")

    di = d.get("design_insights") or []
    dm = d.get("demand_mining") or []
    hn = d.get("hot_news") or []

    for name, items, rng, fields in (
        ("design_insights", di, DI_RANGE, DI_FIELDS),
        ("demand_mining", dm, DM_RANGE, DM_FIELDS),
        ("hot_news", hn, HN_RANGE, HN_FIELDS),
    ):
        if not (rng[0] <= len(items) <= rng[1]):
            err(f"[数量] data/{ds}.json {name}={len(items)}，应在 {rng[0]}-{rng[1]}")
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                err(f"[类型] data/{ds}.json {name}[{i}] 非对象")
                continue
            missing = fields - set(it)
            extra = set(it) - fields
            if missing:
                err(f"[字段] data/{ds}.json {name}[{i}] 缺 {sorted(missing)}")
            if extra:
                warn(f"[字段] data/{ds}.json {name}[{i}] 多出 {sorted(extra)}")
            if not (it.get("title") or "").strip():
                err(f"[内容] data/{ds}.json {name}[{i}] title 为空")

    # validate.py 中的隐含规则
    if dm and di and not (len(dm) < len(di)):
        err(f"[规则] data/{ds}.json demand_mining({len(dm)}) 必须小于 "
            f"design_insights({len(di)})")

    # implication 必须含输入法产品启示（主流程指令的质量要求）
    for i, it in enumerate(di):
        if isinstance(it, dict) and not (it.get("implication") or "").strip():
            err(f"[内容] data/{ds}.json design_insights[{i}] implication 为空")


def check_weekly(monday: date) -> None:
    """校验周报。

    命名约定：周报文件名日期 = 内容周结束后的那个周一（即生成日）。
    例如内容覆盖 2026-08-10~08-16 的周报，文件名是 design-weekly-2026-08-17.json。
    """
    report_day = monday + timedelta(days=7)
    ds = report_day.strftime("%Y-%m-%d")
    path = DATA / f"design-weekly-{ds}.json"
    if not path.exists():
        err(f"[缺失] 周报文件不存在: data/design-weekly-{ds}.json"
            f"（内容周 {monday} ~ {monday + timedelta(days=6)}）")
        return
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        err(f"[损坏] data/design-weekly-{ds}.json 无法解析: {exc}")
        return

    stages = d.get("stages")
    if not isinstance(stages, dict):
        err(f"[字段] data/design-weekly-{ds}.json 缺 stages")
        return

    for s in STAGES:
        if s not in stages:
            err(f"[字段] data/design-weekly-{ds}.json 缺 stage: {s}")
            continue
        items = (stages[s] or {}).get("items") or []
        if len(items) != STAGE_ITEM_COUNT:
            err(f"[数量] design-weekly-{ds} stage {s} 有 {len(items)} 条，"
                f"应为 {STAGE_ITEM_COUNT}")
        for i, it in enumerate(items):
            if not isinstance(it, dict):
                err(f"[类型] design-weekly-{ds} {s}[{i}] 非对象")
                continue
            missing = STAGE_ITEM_FIELDS - set(it)
            if missing:
                err(f"[字段] design-weekly-{ds} {s}[{i}] 缺 {sorted(missing)}")
            for k in ("insight", "action"):
                if not (it.get(k) or "").strip():
                    err(f"[内容] design-weekly-{ds} {s}[{i}] {k} 为空")


def main() -> int:
    args = [a for a in sys.argv[1:] if a]
    if args and args[0] == "--this-week":
        monday = this_monday()
    elif args:
        try:
            monday = datetime.strptime(args[0], "%Y-%m-%d").date()
        except ValueError:
            print(f"✗ 日期格式错误: {args[0]}，应为 YYYY-MM-DD", file=sys.stderr)
            return 1
        if monday.weekday() != 0:
            print(f"✗ {args[0]} 不是周一（weekday={monday.weekday()}）",
                  file=sys.stderr)
            return 1
    else:
        monday = last_monday()

    days = [monday + timedelta(days=i) for i in range(7)]
    report_day = monday + timedelta(days=7)
    print(f"→ 校验周: {monday} ~ {days[-1]}")
    print(f"  日报: data/{days[0]}.json ~ data/{days[-1]}.json")
    print(f"  周报: data/design-weekly-{report_day}.json")
    print()

    for day in days:
        check_daily(day)
    check_weekly(monday)

    # 汇总统计（仅在文件齐全时有意义）
    tot = {"design_insights": 0, "demand_mining": 0, "hot_news": 0}
    found = 0
    for day in days:
        p = DATA / f"{day.strftime('%Y-%m-%d')}.json"
        if not p.exists():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        found += 1
        for k in tot:
            tot[k] += len(d.get(k) or [])

    print(f"日报覆盖: {found}/7 天")
    print(f"累计条目: 设计洞察 {tot['design_insights']} / "
          f"需求挖掘 {tot['demand_mining']} / 热点资讯 {tot['hot_news']}")
    print()

    if warns:
        print(f"⚠ {len(warns)} 条警告（不阻塞）:")
        for w in warns[:10]:
            print(f"   {w}")
        if len(warns) > 10:
            print(f"   ... 另有 {len(warns) - 10} 条")
        print()

    if errors:
        print(f"✗ 门禁未通过：{len(errors)} 项问题", file=sys.stderr)
        for e in errors:
            print(f"   {e}", file=sys.stderr)
        print(file=sys.stderr)
        print("下游动作必须中止：不要 git commit/push，不要发送企微周报，"
              "不要声称本周更新成功。", file=sys.stderr)
        return 1

    print("✅ 门禁通过：7 天日报 + 周报齐全且合规，下游链路可继续。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
