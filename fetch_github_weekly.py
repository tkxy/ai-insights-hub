#!/usr/bin/env python3
"""
fetch_github_weekly.py — 每周一抓取上周 GitHub 热门项目

抓取策略：
  1. 搜索上周创建且 stars > 50 的新项目（新星）
  2. 搜索上周活跃(pushed)且 stars > 2000 的 AI/Agent/LLM 相关项目
  3. 合并去重，按 stars 排序

产出：data/github-weekly/YYYY-Www.json

用法：
    python3 fetch_github_weekly.py           # 当前周（抓上周数据）
    python3 fetch_github_weekly.py 2026-W21  # 指定周
"""

from __future__ import annotations
import json, sys, urllib.request
from datetime import date, timedelta, timezone, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "data" / "github-weekly"

HEADERS = {
    "Accept": "application/vnd.github.v3+json",
    "User-Agent": "ai-insights-hub/1.0",
}

# AI/设计/前端相关关键词
AI_TOPICS = ["ai", "agent", "llm", "machine-learning", "deep-learning",
             "generative-ai", "chatgpt", "claude", "diffusion", "rag"]
DESIGN_TOPICS = ["design", "ui", "canvas", "animation", "3d", "webgl",
                 "react", "svelte", "vue", "tailwind", "motion"]


def iso_week_key(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_range(week_key: str) -> tuple[str, str]:
    """YYYY-Www → (monday, sunday) ISO date strings"""
    parts = week_key.split("-W")
    year, week = int(parts[0]), int(parts[1])
    jan4 = date(year, 1, 4)
    start = jan4 - timedelta(days=jan4.weekday()) + timedelta(weeks=week - 1)
    end = start + timedelta(days=6)
    return start.isoformat(), end.isoformat()


def gh_search(query: str, per_page: int = 20) -> list[dict]:
    """GitHub search API request"""
    url = f"https://api.github.com/search/repositories?q={query}&sort=stars&order=desc&per_page={per_page}"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
            return data.get("items", [])
    except Exception as e:
        print(f"  ! Search failed: {e}", file=sys.stderr)
        return []


def classify_category(repo: dict) -> str:
    """基于 topics/description/language 分类"""
    topics = set(t.lower() for t in (repo.get("topics") or []))
    desc = (repo.get("description") or "").lower()
    name = (repo.get("full_name") or "").lower()

    # AI 优先（最大类别）
    if any(t in topics for t in ["ai", "agent", "llm", "machine-learning", "deep-learning",
                                   "generative-ai", "chatgpt", "claude", "diffusion", "rag",
                                   "artificial-intelligence", "agentic-ai"]):
        return "AI / 生成式"
    if any(kw in desc for kw in ["ai ", "llm", "agent", "gpt", "claude", "generative", "machine learning"]):
        return "AI / 生成式"

    # 3D（严格匹配）
    if any(t in topics for t in ["threejs", "webgl", "webgpu", "3d", "spatial", "xr", "vr", "ar"]):
        return "3D / 空间计算"
    if "three.js" in desc or "webgl" in desc or "3d " in desc or "react-three" in name:
        return "3D / 空间计算"

    # 画布
    if any(t in topics for t in ["canvas", "whiteboard", "diagram", "flowchart"]):
        return "画布 / 空间界面"
    if any(kw in desc for kw in ["canvas", "whiteboard", "infinite canvas", "node editor", "flow"]):
        return "画布 / 空间界面"

    # 动效
    if any(t in topics for t in ["animation", "motion", "transition", "gsap", "lottie"]):
        return "动效 / 交互编排"
    if any(kw in desc for kw in ["animation", "motion", "transition", "animate"]):
        return "动效 / 交互编排"

    # 文本
    if any(t in topics for t in ["font", "typography", "markdown", "editor", "text"]):
        return "文本 / 排版"

    # UI/组件
    if any(t in topics for t in ["ui", "component", "design-system", "tailwind", "css"]):
        return "UI / 组件"
    if any(kw in desc for kw in ["ui ", "component", "design system", "tailwind"]):
        return "UI / 组件"

    # 开发工具
    if any(t in topics for t in ["cli", "terminal", "devtool", "developer-tools"]):
        return "开发工具"

    return "其他"


def generate_insight(repo: dict) -> str:
    """基于 description 和 category 生成简短中文洞察"""
    desc = repo.get("description") or ""
    cat = repo.get("_category", "")
    name = repo.get("full_name", "")

    # 简单规则生成
    if "agent" in desc.lower() or "agent" in cat.lower():
        return f"Agent 生态持续扩展——{name.split('/')[-1]} 代表了社区对 AI 自主能力的持续探索。"
    if "canvas" in desc.lower() or "画布" in cat:
        return "画布型产品持续火热，空间组织正在替代线性列表成为复杂信息的主要交互形式。"
    if "3d" in cat.lower() or "three" in desc.lower():
        return "3D/空间技术在 Web 端的可用性持续提升，从营销装饰走向产品交互层。"
    if "motion" in desc.lower() or "animation" in desc.lower() or "动效" in cat:
        return "动效工具链正在成熟——从「手动写 CSS transition」到「可编排、可复用的运动组件」。"
    if "ui" in cat.lower() or "component" in desc.lower():
        return "组件库竞争进入差异化阶段，设计工程师需要的不只是样式，还有交互行为和品牌动效。"
    return f"值得关注的活跃项目，上周获得了大量社区关注。"


def generate_cases(repo: dict) -> list[dict]:
    """基于 category 和 description 生成应用案例"""
    cat = repo.get("_category", "")
    desc = (repo.get("description") or "").lower()
    name = (repo.get("full_name") or "").split("/")[-1].lower()

    cases_map = {
        "AI / 生成式": [
            {"name": "智能对话产品", "desc": "集成到客服、助手类产品中提升对话质量和自主能力"},
            {"name": "内容生产工具", "desc": "文案、代码、图片等内容的 AI 辅助生成"},
            {"name": "企业知识库", "desc": "基于 RAG 的企业文档问答和知识检索"},
        ],
        "画布 / 空间界面": [
            {"name": "协作白板", "desc": "团队脑暴、需求评审、用户旅程图的可视化协作"},
            {"name": "AI 工作流编排", "desc": "Agent/自动化任务的节点式可视化配置"},
            {"name": "知识图谱可视化", "desc": "将文档/概念关系以画布形式空间化呈现"},
        ],
        "动效 / 交互编排": [
            {"name": "品牌动效落地", "desc": "产品首页、活动页的入场动画和滚动交互"},
            {"name": "组件交互反馈", "desc": "按钮、卡片、列表的微交互和状态转换动效"},
            {"name": "设计系统 Motion Token", "desc": "将动效参数化为设计系统的一部分"},
        ],
        "3D / 空间计算": [
            {"name": "产品 3D 展示", "desc": "电商商品、硬件产品的在线 3D 预览和交互"},
            {"name": "数字孪生", "desc": "园区/工厂的 3D 实时监控和数据叠加"},
            {"name": "XR 应用界面", "desc": "VR/AR 场景中的空间 UI 和交互设计"},
        ],
        "UI / 组件": [
            {"name": "设计系统建设", "desc": "企业级组件库的搭建和跨团队规范落地"},
            {"name": "快速原型搭建", "desc": "用现成组件快速实现产品原型和 Demo"},
            {"name": "品牌差异化", "desc": "在通用组件上叠加品牌动效和视觉个性"},
        ],
        "文本 / 排版": [
            {"name": "富文本编辑器", "desc": "文档、笔记、CMS 的编辑体验优化"},
            {"name": "排版动画", "desc": "标题、段落的动态排版和进场效果"},
            {"name": "多语言适配", "desc": "CJK 文字排版的特殊处理和优化"},
        ],
        "开发工具": [
            {"name": "开发者体验提升", "desc": "CLI 工具、终端美化、开发流程自动化"},
            {"name": "项目脚手架", "desc": "快速初始化项目结构和配置"},
            {"name": "调试与监控", "desc": "开发阶段的性能分析和问题定位"},
        ],
    }

    # 特殊项目的精确案例
    if "prompt" in name or "prompt" in desc:
        return [
            {"name": "Prompt 工程优化", "desc": "系统提示词的管理、版本控制和 A/B 测试"},
            {"name": "AI 应用调优", "desc": "通过 prompt 模板提升生成质量和一致性"},
            {"name": "团队 Prompt 库", "desc": "跨团队共享和复用高质量提示词"},
        ]
    if "workflow" in name or "automation" in desc or "n8n" in name:
        return [
            {"name": "业务流程自动化", "desc": "将重复性工作编排为自动化流程"},
            {"name": "AI + 工具编排", "desc": "让 AI 调用多个工具完成复杂任务"},
            {"name": "数据管线配置", "desc": "ETL/数据同步流程的可视化编排"},
        ]
    if "code" in name or "coding" in desc or "copilot" in desc:
        return [
            {"name": "编码效率提升", "desc": "代码补全、重构建议、Bug 定位辅助"},
            {"name": "代码审查自动化", "desc": "PR Review 的 AI 辅助和规范检查"},
            {"name": "技术文档生成", "desc": "从代码自动生成 API 文档和注释"},
        ]

    return cases_map.get(cat, [
        {"name": "技术选型参考", "desc": "了解该领域最新技术方向和社区趋势"},
        {"name": "团队分享素材", "desc": "适合作为技术分享或设计组内部交流的案例"},
    ])


def main() -> int:
    # 确定目标周
    if len(sys.argv) > 1:
        target_week = sys.argv[1]
    else:
        # 当前周 = 上周数据
        today = date.today()
        last_week = today - timedelta(days=7)
        target_week = iso_week_key(last_week)

    start_date, end_date = week_range(target_week)
    print(f"→ Fetching GitHub trending for {target_week} ({start_date} ~ {end_date})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{target_week}.json"

    seen = {}
    all_repos = []

    # 1. 上周新创建且有一定 stars 的项目
    print("  · Searching new repos created last week...")
    new_repos = gh_search(f"created:{start_date}..{end_date}+stars:>50", per_page=20)
    for r in new_repos:
        if r["full_name"] not in seen:
            seen[r["full_name"]] = True
            all_repos.append(r)

    # 2. AI/Agent 相关活跃项目
    print("  · Searching AI/Agent active repos...")
    for topic in ["ai", "agent", "llm"]:
        items = gh_search(f"topic:{topic}+pushed:{start_date}..{end_date}+stars:>2000", per_page=10)
        for r in items:
            if r["full_name"] not in seen:
                seen[r["full_name"]] = True
                all_repos.append(r)

    # 3. 设计/前端相关活跃项目
    print("  · Searching design/frontend active repos...")
    for topic in ["canvas", "animation", "3d", "ui"]:
        items = gh_search(f"topic:{topic}+pushed:{start_date}..{end_date}+stars:>500", per_page=8)
        for r in items:
            if r["full_name"] not in seen:
                seen[r["full_name"]] = True
                all_repos.append(r)

    # 排序
    all_repos.sort(key=lambda x: x.get("stargazers_count", 0), reverse=True)

    # 只保留前 20 个
    top_repos = all_repos[:20]

    # 格式化输出
    items = []
    for r in top_repos:
        cat = classify_category(r)
        r["_category"] = cat
        items.append({
            "name": r["full_name"],
            "title": r.get("description") or "暂无描述",
            "url": r["html_url"],
            "stars": r["stargazers_count"],
            "forks": r["forks_count"],
            "language": r.get("language"),
            "topics": (r.get("topics") or [])[:5],
            "category": cat,
            "insight": generate_insight(r),
            "cases": generate_cases(r),
            "isNew": r["created_at"][:10] >= start_date,
        })

    output = {
        "week": target_week,
        "range": {"start": start_date, "end": end_date},
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
        "items": items,
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✓ Saved {len(items)} repos → {out_path}")

    # 更新 index
    index_path = OUT_DIR / "index.json"
    if index_path.exists():
        idx = json.loads(index_path.read_text())
    else:
        idx = {"weeks": []}

    # 去重添加
    existing_weeks = {w["week"] for w in idx["weeks"]}
    if target_week not in existing_weeks:
        idx["weeks"].insert(0, {
            "week": target_week,
            "start": start_date,
            "end": end_date,
            "count": len(items),
        })
    idx["weeks"].sort(key=lambda x: x["week"], reverse=True)
    idx["updatedAt"] = datetime.now(timezone.utc).isoformat()

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)

    print(f"✓ Updated index.json ({len(idx['weeks'])} weeks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
