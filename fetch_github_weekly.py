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
import json, sys, urllib.request, re, html
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



def parse_count(text: str) -> int:
    text = (text or '').strip().lower().replace(',', '')
    if not text:
        return 0
    if text.endswith('k'):
        return int(float(text[:-1]) * 1000)
    if text.endswith('m'):
        return int(float(text[:-1]) * 1000000)
    return int(float(text))


def clean_html(fragment: str) -> str:
    return html.unescape(re.sub(r'\s+', ' ', re.sub(r'<.*?>', '', fragment or '')).strip())


def fetch_github_trending_weekly() -> list[dict]:
    """Scrape https://github.com/trending?since=weekly for actual weekly hot repos.
    GitHub Search API sorted by total stars is too stable; Trending exposes stars gained this week.
    """
    url = 'https://github.com/trending?since=weekly'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=25) as resp:
        page = resp.read().decode('utf-8', 'ignore')
    articles = re.findall(r'<article class="Box-row">([\s\S]*?)</article>', page)
    repos = []
    for a in articles:
        m = re.search(r'<h2[^>]*>[\s\S]*?<a[^>]*href="/([^"]+)"[^>]*>', a)
        if not m:
            continue
        full_name = m.group(1).strip()
        desc_m = re.search(r'<p[^>]*>([\s\S]*?)</p>', a)
        desc = clean_html(desc_m.group(1)) if desc_m else ''
        # GitHub trending 的 p 标签里有时会混入 “Sponsor Star owner / repo” 操作文案，清掉再展示
        desc = re.sub(r'^(Sponsor\s+)?Star\s+[A-Za-z0-9_.-]+\s*/\s*[A-Za-z0-9_.-]+\s*', '', desc).strip()
        lang_m = re.search(r'<span itemprop="programmingLanguage">([^<]+)</span>', a)
        language = clean_html(lang_m.group(1)) if lang_m else None
        star_m = re.search(r'aria-label="([0-9,]+) users starred', a)
        if star_m:
            stars = parse_count(star_m.group(1))
        else:
            link_m = re.search(r'href="/[^"]+/stargazers"[^>]*>([\s\S]*?)</a>', a)
            if link_m:
                stars = parse_count(clean_html(link_m.group(1)))
            else:
                count_m = re.search(r'<span[^>]*class="[^"]*social-count[^"]*"[^>]*>([\s\S]*?)</span>', a)
                stars = parse_count(clean_html(count_m.group(1))) if count_m else 0
        week_m = re.search(r'([0-9,]+) stars? this week', a)
        weekly_stars = parse_count(week_m.group(1)) if week_m else 0
        fork_m = re.search(r'/network/members"[^>]*>[\s\S]*?([0-9,.]+[kKmM]?)\s*</a>', a)
        forks = parse_count(fork_m.group(1)) if fork_m else 0
        repos.append({
            'full_name': full_name,
            'description': desc,
            'html_url': 'https://github.com/' + full_name,
            'stargazers_count': stars,
            'forks_count': forks,
            'language': language,
            'topics': [],
            'weekly_stars': weekly_stars,
            'created_at': '',
        })
    return repos


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
    """基于项目语义生成真正的产品/技术洞察，而不是“很火”式废话"""
    desc = repo.get("description") or ""
    full_name = repo.get("full_name", "")
    text = (full_name + " " + desc).lower()
    repo_name = (full_name.split("/")[-1] or "这个项目")

    specific = {
        "Lum1104/Understand-Anything": "它的重点不是画图，而是把复杂概念转成能教学的结构图。GitHub 用户追捧这类项目，说明 AI 可视化的价值正在从「看起来厉害」转向「真的帮人理解」。",
        "colbymchenry/codegraph": "代码库上下文正在从文本块走向知识图谱。对大型工程来说，先建立代码关系图，再让 AI 推理，比把文件一股脑塞进上下文更可控。",
        "Panniantong/Agent-Reach": "Agent 正在从聊天窗口走向主动触达和任务执行。值得关注的是它如何把用户目标、外部渠道和自动行动串起来，而不是又做一个问答机器人。",
        "roboflow/supervision": "视觉 AI 的工程化门槛正在降低。数据标注、检测、追踪、评估这些能力被封装成工具链后，普通产品团队也能更快把 CV 能力嵌进业务流程。",
        "mukul975/Anthropic-Cybersecurity-Skills": "安全领域走向 skill 化，说明专业 Agent 不再靠一段通用 prompt，而需要可审计的任务清单、知识包和操作流程。",
        "run-llama/liteparse": "文档解析是 RAG 的地基。liteparse 这类项目热起来，说明大家已经意识到：答案质量很大程度取决于前面有没有把文档结构解析干净。",
        "microsoft/agent-governance-toolkit": "Agent 进入企业后，治理会比能力更先成为瓶颈。权限、策略、审计和评估工具被微软单独做成 toolkit，说明 Agent 正在从 demo 走向生产系统。"
    }
    if full_name in specific:
        return specific[full_name]

    # 先处理近期 GitHub AI 工程高频项目类型
    if any(k in text for k in ["tool outputs", "logs", "compress", "token", "context"]):
        return "AI 工程的瓶颈正在从模型能力转向上下文成本。能压缩日志、工具输出和 RAG chunk 的项目走热，说明开发者开始认真治理「给模型看的信息」，而不是一味堆更大的上下文窗口。"
    if any(k in text for k in ["markdown", "office documents", "document parser", "parse", "pdf", "docx"]):
        return "文档解析正在成为 RAG 和企业 AI 的基础设施。真正难的不是把文件转成文本，而是稳定保留结构、表格、标题层级，让后续问答和总结有可靠上下文。"
    if any(k in text for k in ["agent harness", "performance optimization", "skills", "instincts", "memory", "security"]):
        return "Agent 产品竞争开始进入「运行框架」阶段。社区关注的不再只是接哪个模型，而是技能、记忆、安全、评估这些让 Agent 长期稳定工作的底层系统。"
    if any(k in text for k in ["agent that grows", "hermes agent", "hermes webui"]):
        return "个人 Agent 的方向正在从一次性问答转向可成长系统。它需要记忆、偏好、工具和 UI 承载，像一个长期协作对象，而不是每次都从零开始的聊天窗口。"
    if any(k in text for k in ["short videos", "video", "moneyprinter", "generate short"]):
        return "生成式视频正在从专业创作工具下沉为自动化流水线。一个脚本能把选题、文案、素材、配音、剪辑串起来，意味着内容生产的门槛被压到运营级别。"
    if any(k in text for k in ["good taste", "design language", "slop", "ai tells", "prose"]):
        return "AI 生成内容的下一阶段不是更能写，而是更像人、更有审美。社区开始把「去 AI 味」「设计品味」「语言质感」做成可复用 skill，说明输出质量正在从功能问题变成风格问题。"
    if any(k in text for k in ["notebook", "notebook lm", "knowledge", "researches any topic", "last30days"]):
        return "知识工作正在从文件管理变成研究代理。用户需要的不是又一个笔记库，而是能按时间、主题和来源自动整理材料、形成判断的研究系统。"
    if any(k in text for k in ["code knowledge graph", "codegraph", "pre-indexed code"]):
        return "代码库理解正在图谱化。相比把整个仓库塞进上下文，预先建立代码知识图谱更适合大型项目里的定位、影响分析和跨文件推理。"
    if any(k in text for k in ["governance", "policy", "evaluation", "compliance"]):
        return "Agent 落地进入治理阶段。企业真正担心的不是能不能调用工具，而是权限边界、策略约束、审计和失败兜底是否可控。"
    if any(k in text for k in ["cybersecurity", "security skills", "structured cybersecurity"]):
        return "垂直领域 skill 正在成为 Agent 能力分发方式。安全、法务、研究这类专业任务不适合靠通用 prompt 硬写，而需要结构化知识包和操作流程。"
    if any(k in text for k in ["graphs that teach", "understand anything", "teach", "visual"]):
        return "可解释可视化比炫技图表更有价值。这个方向说明用户要的不是漂亮图，而是能帮助理解复杂概念的交互式解释结构。"
    if any(k in text for k in ["learn it", "from scratch", "engineering"]):
        return "AI 工程教育内容走热，说明开发者正在从「会调 API」转向系统理解训练、推理、评估、部署全链路。团队内部能力建设会成为 AI 产品速度差异。"
    if any(k in text for k in ["voice", "vtuber", "hands-free"]):
        return "AI 交互正在从文字框走向语音和人格化形态。免手操作、实时语音和虚拟形象结合，会让助手更像陪伴式界面，而不是工具面板。"

    # 通用兜底：按类别给可行动洞察
    cat = repo.get("_category", "")
    if "AI" in cat:
        return f"{repo_name} 的走热说明 AI 应用正在拆成更小的能力模块。值得看的不是项目本身多火，而是它解决了 AI 产品链路里的一个具体断点：上下文、记忆、工具、内容生成或评估。"
    if "画布" in cat:
        return "画布型交互继续升温，说明复杂任务更适合空间化组织。对设计来说，画布不是白板皮肤，而是一套选择、拖拽、连接、撤销和协作的基础语言。"
    if "动效" in cat:
        return "动效正在从装饰变成产品反馈机制。被关注的动效项目通常不是为了炫，而是让状态变化、因果关系和操作结果更容易被用户感知。"
    if "3D" in cat:
        return "3D Web 项目的关注上升，说明空间界面正在从营销页走向真实产品层。设计师需要开始理解场景、视角、遮挡和空间控件，而不只是平面组件。"
    if "UI" in cat:
        return "UI 组件项目的热度反映出设计工程化继续加速。现在的组件不只是样式复用，还要承载动效、状态、可访问性和品牌表达。"
    return f"{repo_name} 不是简单的热门项目，它代表了一个具体技术方向正在被社区快速验证。需要结合它解决的问题判断是否能迁移到我们的输入法 AI、知识管理或设计工程流程里。"


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

    # 1. 优先抓 GitHub Trending weekly（真实本周热度）
    print("  · Scraping GitHub Trending weekly...")
    try:
        for r in fetch_github_trending_weekly():
            if r["full_name"] not in seen:
                seen[r["full_name"]] = True
                all_repos.append(r)
    except Exception as e:
        print(f"  ! Trending scrape failed: {e}", file=sys.stderr)

    # 2. 兜底：上周新创建且有一定 stars 的项目
    if len(all_repos) < 10:
        print("  · Searching new repos created last week...")
        new_repos = gh_search(f"created:{start_date}..{end_date}+stars:>50", per_page=20)
        for r in new_repos:
            if r["full_name"] not in seen:
                seen[r["full_name"]] = True
                all_repos.append(r)

    # 3. 兜底：AI/Agent/设计相关活跃项目
    if len(all_repos) < 20:
        print("  · Searching AI/Agent/design active repos...")
        for topic in ["ai", "agent", "llm", "canvas", "animation", "3d", "ui"]:
            items = gh_search(f"topic:{topic}+pushed:{start_date}..{end_date}+stars:>500", per_page=8)
            for r in items:
                if r["full_name"] not in seen:
                    seen[r["full_name"]] = True
                    all_repos.append(r)

    # 排序
    all_repos.sort(key=lambda x: (x.get("weekly_stars", 0), x.get("stargazers_count", 0)), reverse=True)

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
            "stars": r.get("stargazers_count", 0),
            "weeklyStars": r.get("weekly_stars", 0),
            "forks": r.get("forks_count", 0),
            "language": r.get("language"),
            "topics": (r.get("topics") or [])[:5],
            "category": cat,
            "insight": generate_insight(r),
            "cases": generate_cases(r),
            "isNew": (r.get("created_at") or "")[:10] >= start_date,
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
