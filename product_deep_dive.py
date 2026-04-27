#!/usr/bin/env python3
"""
产品深度报告生成器
-----------------------------------------
每周一 09:00 跑一次：
1. 拉取 CloudIDE 截图池过去 7 天的截图
2. 用 Gemini 选出「最值得深度报道」的一个产品
3. 用 Gemini + Google Search Grounding 搜产品官网/评测/更新日志
4. 生成结构化报告 JSON 并写入 ai-insights-hub/data/
5. 同步截图到 assets/deep-dive/<slug>-<date>/
6. git add/commit/push -> GitHub Pages 部署
7. 成功 / 失败推企微群告警
"""

import os
import re
import sys
import json
import time
import shutil
import logging
import hashlib
import traceback
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.parse import urljoin

# ---------------------- 常量 / 环境 ----------------------
ROOT = Path(__file__).resolve().parent            # ~/ai-insights-hub
DATA_DIR = ROOT / "data"
ASSETS_DIR = ROOT / "assets" / "deep-dive"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

# 从 ~/.workbuddy/secrets/wecom-competitor-bot.env 读复用的 key
SECRET_ENV = Path.home() / ".workbuddy" / "secrets" / "wecom-competitor-bot.env"


def load_env():
    if SECRET_ENV.exists():
        for line in SECRET_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

H5_API_BASE = os.environ.get("H5_API_BASE", "").rstrip("/")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
WECOM_BOT_ID = os.environ.get("WECOM_BOT_ID", "")
WECOM_BOT_SECRET = os.environ.get("WECOM_BOT_SECRET", "")

GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_MODEL_FALLBACK = "gemini-2.5-flash-lite"
GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"

# ---------------------- 日志 ----------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "deep-dive.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("deep-dive")


# ---------------------- HTTP 工具 ----------------------
def http_get_json(url, timeout=30):
    req = Request(url, headers={"User-Agent": "ai-insights-deep-dive/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def http_get_bytes(url, timeout=60):
    req = Request(url, headers={"User-Agent": "ai-insights-deep-dive/1.0"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()


def http_post_json(url, payload, timeout=120):
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ---------------------- 数据拉取 ----------------------
def fetch_screenshots_last_week():
    """拉过去 7 天的截图（按 date 字段过滤）"""
    if not H5_API_BASE:
        raise RuntimeError("H5_API_BASE 未配置")
    url = f"{H5_API_BASE}/api/screenshots"
    data = http_get_json(url)
    items = data.get("items", [])

    today = datetime.now().date()
    cutoff = today - timedelta(days=7)

    filtered = []
    for it in items:
        d = it.get("date") or it.get("uploadDate", "")[:10]
        try:
            dt = datetime.strptime(d[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if dt >= cutoff:
            filtered.append(it)
    log.info(f"拉到 {len(items)} 张图，过去 7 天 {len(filtered)} 张")
    return filtered


# ---------------------- Gemini 调用 ----------------------
def gemini_call(model, contents, tools=None, system=None, timeout=180, retries=3, max_tokens=8192):
    """统一入口，带重试 + fallback 降级"""
    url = f"{GEMINI_ENDPOINT}/{model}:generateContent?key={GEMINI_API_KEY}"
    body = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "temperature": 0.7,
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if tools:
        body["tools"] = tools

    last_err = None
    for attempt in range(retries):
        try:
            return http_post_json(url, body, timeout=timeout)
        except Exception as e:
            last_err = e
            msg = str(e)
            if "503" in msg or "high demand" in msg.lower() or "429" in msg or "overloaded" in msg.lower():
                # 前 retries-1 次在主模型上重试
                if attempt < retries - 1:
                    wait = 3 * (attempt + 1)
                    log.warning(f"{model} 限流，{wait}s 后重试 ({attempt+1}/{retries})")
                    time.sleep(wait)
                    continue
                # 最后一次降级到 lite（注意：lite 不支持 grounding，要剥 tools）
                log.warning(f"{model} 重试耗尽，降级到 {GEMINI_MODEL_FALLBACK}")
                url2 = f"{GEMINI_ENDPOINT}/{GEMINI_MODEL_FALLBACK}:generateContent?key={GEMINI_API_KEY}"
                body2 = {k: v for k, v in body.items() if k != "tools"}  # lite 不支持 googleSearch
                return http_post_json(url2, body2, timeout=timeout)
            # 非限流错误直接抛
            raise
    raise last_err or RuntimeError("Gemini 调用失败")


def extract_text(resp):
    """从 Gemini 响应里抠文本"""
    try:
        return resp["candidates"][0]["content"]["parts"][0]["text"]
    except Exception:
        return ""


def extract_grounding(resp):
    """从 grounding 响应里抠引用源"""
    sources = []
    try:
        chunks = resp["candidates"][0].get("groundingMetadata", {}).get("groundingChunks", [])
        for c in chunks:
            web = c.get("web") or {}
            if web.get("uri"):
                sources.append({
                    "title": web.get("title", "参考资料"),
                    "url": web["uri"],
                })
    except Exception:
        pass
    # 去重
    seen = set()
    out = []
    for s in sources:
        if s["url"] in seen:
            continue
        seen.add(s["url"])
        out.append(s)
    return out


def strip_json_fence(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def extract_json_block(text):
    """从任意文本里挖出第一个完整的 JSON 对象（容错 Gemini 在 JSON 前后加自然语言的情况）"""
    text = strip_json_fence(text)
    # 先直接尝试
    try:
        return json.loads(text)
    except Exception:
        pass
    # 再尝试找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        block = text[start : end + 1]
        try:
            return json.loads(block)
        except Exception:
            pass
    # 最后尝试括号匹配扫描，拿第一个平衡的 { ... }
    if start >= 0:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    block = text[start : i + 1]
                    try:
                        return json.loads(block)
                    except Exception:
                        break
    raise ValueError(f"无法从响应中解析出 JSON（前 300 字符）：{text[:300]}")


# ---------------------- Step 1: 选品 ----------------------
def pick_product(screenshots):
    """让 Gemini 看池子本周的图，挑一个最值得深度报道的产品"""
    # 过滤掉模糊分组
    VAGUE = {"其他", "未知", "未分类", "other", "unknown", ""}

    # 按 competitor 聚合
    agg = {}
    for s in screenshots:
        name = (s.get("competitorName") or s.get("competitor") or "").strip()
        if name in VAGUE:
            # 尝试从 title / summary / tags 里提取具体产品名
            text_blob = (s.get("title", "") + " " + s.get("summary", "") + " " + " ".join(s.get("tags", []))).lower()
            # 常见具体产品关键词（命中就改分组）
            for kw in ["typeless", "notion", "chatgpt", "claude", "perplexity",
                       "cursor", "raycast", "arc", "linear", "figma",
                       "heptabase", "obsidian", "craft", "reor", "抖音",
                       "小红书", "微信", "飞书", "钉钉", "讯飞", "百度",
                       "搜狗", "华为", "小米"]:
                if kw in text_blob:
                    name = kw
                    break
            else:
                continue  # 仍然模糊，跳过
        agg.setdefault(name, []).append(s)

    if not agg:
        raise RuntimeError("本周截图中未能识别出任何明确产品（全部为模糊分组）")

    summary = []
    for name, items in agg.items():
        titles = [it.get("title", "") for it in items[:5]]
        cats = list({it.get("categoryName", "") for it in items})
        summary.append({
            "name": name,
            "count": len(items),
            "categories": cats,
            "sample_titles": titles,
        })
    # 按 count 倒序给 AI 看
    summary.sort(key=lambda x: -x["count"])

    prompt = f"""下面是本周竞品截图池里收集到的内容（按产品聚合，已过滤掉"其他/未分类"模糊分组）：

{json.dumps(summary, ensure_ascii=False, indent=2)}

请帮我挑一个**最值得这周深度报道**的产品。选品标准（按重要性递减）：
1. 产品有明显的「交互创新」或「视觉动效亮点」值得设计师学习（最重要）
2. 产品热度高 / 截图量多（代表关注度）
3. 必须是**具体产品名**（从上表 name 字段里选，不要自己造词）

**输出 JSON（严格格式，不要任何多余文字）**：
{{
  "productName": "必须从上表 name 字段里选一个",
  "productSlug": "英文短名小写连字符，如 typeless",
  "reason": "一句话说明为什么选它（20字内）",
  "focusAngle": "这周围绕它主要想聊什么（15字内，如：语音交互创新、动效设计）"
}}"""

    resp = gemini_call(GEMINI_MODEL, [{"parts": [{"text": prompt}]}], max_tokens=2048)
    try:
        pick = extract_json_block(extract_text(resp))
    except Exception as e:
        raise RuntimeError(f"选品 JSON 解析失败: {e}")

    # 校验：选的必须在 agg 里
    chosen_name = pick["productName"]
    if chosen_name not in agg:
        # 模糊匹配
        match = None
        for k in agg:
            if chosen_name.lower() in k.lower() or k.lower() in chosen_name.lower():
                match = k
                break
        if match:
            chosen_name = match
            pick["productName"] = match
        else:
            # 兜底：取截图量最多的
            chosen_name = max(agg, key=lambda k: len(agg[k]))
            pick["productName"] = chosen_name
            pick["reason"] = (pick.get("reason", "") + "（AI选品不在列表，降级为截图量最多）").strip()

    pick["screenshots"] = agg[chosen_name][:12]
    log.info(f"选品：{chosen_name} | 理由：{pick.get('reason')} | 截图 {len(pick['screenshots'])} 张")
    return pick


# ---------------------- Step 2: 联网搜料 + 生成报告 ----------------------
def generate_report(pick):
    """用 Gemini + Google Search Grounding 深度调研 + 生成报告"""
    product = pick["productName"]
    angle = pick.get("focusAngle", "")

    # 截图简要摘要（给 Gemini 当分析素材）
    shots_brief = []
    for s in pick["screenshots"][:12]:
        shots_brief.append({
            "title": s.get("title", ""),
            "category": s.get("categoryName", ""),
            "summary": s.get("summary", ""),
            "tags": s.get("tags", []),
        })

    system_inst = """你是腾讯搜狗输入法的资深设计师 + 竞品研究员。
你的职责：
- 读懂截图反映的产品能力
- 用 Google 搜产品公开资料（官网、媒体评测、版本更新日志、视频 Demo）
- 交叉验证，写一份**对搜狗输入法团队有借鉴价值**的产品深度报告

写作要求：
- 避免官话套话，说人话
- 对搜狗的启示要**具体可落地**，不要"建议加强 XXX"这种废话
- 核心交互创新要用"观察-推断-启示"三段式展开"""

    prompt = f"""请对「{product}」做一份产品深度报告，重点方向：{angle}

**本周池子里这个产品的截图摘要（供你分析用）**：
{json.dumps(shots_brief, ensure_ascii=False, indent=2)}

**请用 Google 搜索补充以下信息**：
1. 产品官网 / 发布团队 / 核心定位
2. 近期版本更新或媒体评测（优先 2026 年最近 3 个月）
3. 用户口碑 / 差异化卖点

**输出 JSON（严格格式，不要任何 markdown 或多余文字）**：
{{
  "productName": "{product}",
  "productSlug": "{pick['productSlug']}",
  "title": "标题（20字内，要有吸引力）",
  "subtitle": "副标题（30字内，点出核心看点）",
  "overview": {{
    "what": "一句话说这是什么产品",
    "scene": "主打场景（3-5个关键词）",
    "audience": "目标用户（一句话）",
    "team": "开发团队 / 公司",
    "launchDate": "首发时间或最新版本时间"
  }},
  "innovations": [
    {{
      "title": "交互创新点标题（短）",
      "icon": "相关 emoji 一个",
      "observation": "观察到什么（从截图+资料中看到的具体现象）",
      "insight": "为什么这样设计（设计意图推断）",
      "sogouImplication": "对搜狗输入法的启示（具体可落地）",
      "screenshotHint": "如果有截图对应，简短描述该看哪张（可留空）"
    }}
  ],
  "visualMotion": {{
    "summary": "视觉/动效整体风格一句话",
    "highlights": ["亮点1", "亮点2", "亮点3"]
  }},
  "differentiation": {{
    "summary": "它和同品类竞品最不一样的点",
    "positioning": "在市场里占什么位（小众精品/大众旗舰/挑战者...）"
  }},
  "sogouTakeaways": [
    "对搜狗输入法团队的核心启示 1（具体，可落地）",
    "对搜狗输入法团队的核心启示 2",
    "对搜狗输入法团队的核心启示 3"
  ]
}}

innovations 写 3-5 条，sogouTakeaways 写 3-5 条。语言简洁，避免废话。

**极重要**：直接以左花括号开头输出 JSON 对象，不要任何前言、标题、副标题说明、markdown 代码块围栏或其他文字。整个响应 = 一个合法 JSON。"""

    # 启用 Google Search Grounding
    tools = [{"googleSearch": {}}]
    contents = [{"parts": [{"text": prompt}]}]

    resp = gemini_call(GEMINI_MODEL, contents, tools=tools, system=system_inst, timeout=240, max_tokens=8192)
    text = extract_text(resp)
    sources = extract_grounding(resp)

    try:
        report = extract_json_block(text)
    except Exception as e:
        log.error(f"报告 JSON 解析失败（前 400 字符）: {text[:400]}")
        raise RuntimeError(f"报告生成失败: {e}")

    # 注入元数据
    today = datetime.now()
    report["generatedAt"] = today.strftime("%Y-%m-%d %H:%M")
    report["dateOf"] = today.strftime("%Y-%m-%d")
    report["reportType"] = "deep-dive"
    report["selectionReason"] = pick.get("reason", "")
    report["sources"] = sources
    return report


# ---------------------- Step 3: 落图 + 落盘 ----------------------
def slugify(name):
    s = re.sub(r"[^a-zA-Z0-9一-龥]+", "-", name).strip("-").lower()
    return s or "product"


def save_screenshots(pick, date_str):
    """把选中产品的截图下载到 assets/deep-dive/{slug}-{date}/"""
    slug = pick["productSlug"]
    target = ASSETS_DIR / f"{slug}-{date_str}"
    target.mkdir(parents=True, exist_ok=True)

    saved = []
    for s in pick["screenshots"][:12]:
        src_url = s.get("url", "")
        if not src_url:
            continue
        if src_url.startswith("/"):
            src_url = H5_API_BASE + src_url
        filename = s.get("filename") or f"{s.get('id', 'img')}.jpg"
        # 保存为 .jpg 统一压缩（其实直接保原扩展名更简单）
        ext = Path(filename).suffix or ".jpg"
        safe_name = re.sub(r"[^a-zA-Z0-9._-]", "", s.get("id", "img")) + ext
        dst = target / safe_name
        try:
            raw = http_get_bytes(src_url)
            dst.write_bytes(raw)
            saved.append({
                "url": f"assets/deep-dive/{slug}-{date_str}/{safe_name}",
                "title": s.get("title", ""),
                "caption": s.get("summary", ""),
                "category": s.get("categoryName", ""),
            })
        except Exception as e:
            log.warning(f"下载失败 {src_url}: {e}")
    log.info(f"保存 {len(saved)} 张截图到 {target}")
    return saved


def write_report(report, screenshots_meta):
    """写单篇 JSON + 更新 index"""
    slug = report["productSlug"]
    date_str = report["dateOf"]
    report["screenshots"] = screenshots_meta

    # 单篇
    fn = DATA_DIR / f"deep-dive-{slug}-{date_str}.json"
    fn.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"写入 {fn}")

    # 更新 index
    idx_path = DATA_DIR / "deep-dive-index.json"
    if idx_path.exists():
        idx = json.loads(idx_path.read_text(encoding="utf-8"))
    else:
        idx = {"reports": []}

    entry = {
        "id": f"{slug}-{date_str}",
        "slug": slug,
        "dateOf": date_str,
        "productName": report["productName"],
        "title": report["title"],
        "subtitle": report.get("subtitle", ""),
        "generatedAt": report["generatedAt"],
        "selectionReason": report.get("selectionReason", ""),
        "thumbs": [s["url"] for s in screenshots_meta[:4]],
        "totalImages": len(screenshots_meta),
        "innovationCount": len(report.get("innovations", [])),
    }
    # 去重：同 id 覆盖
    idx["reports"] = [r for r in idx["reports"] if r.get("id") != entry["id"]]
    idx["reports"].insert(0, entry)
    idx["updatedAt"] = report["generatedAt"]
    idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"更新 {idx_path}")
    return fn


# ---------------------- Step 4: Git 推送 ----------------------
def git_push():
    def run(cmd):
        log.info(f"$ {' '.join(cmd)}")
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            log.warning(f"git stderr: {r.stderr}")
        return r

    run(["git", "add", "data/", "assets/deep-dive/"])
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True
    ).stdout
    if not status.strip():
        log.info("无文件变更，跳过 push")
        return False
    today = datetime.now().strftime("%Y-%m-%d")
    run(["git", "commit", "-m", f"chore: 产品深度报告 {today}"])
    r = run(["git", "push"])
    return r.returncode == 0


# ---------------------- Step 5: 企微告警 ----------------------
def wecom_notify(text):
    if not (WECOM_BOT_ID and WECOM_BOT_SECRET):
        return
    # TODO: 如有群机器人 webhook 另行接入。目前只写日志
    log.info(f"[通知] {text}")


# ---------------------- Main ----------------------
def main():
    log.info("=" * 60)
    log.info("产品深度报告生成开始")

    # 环境开关：SKIP_PUSH=1 时不 git push（便于本地验证）
    skip_push = os.environ.get("SKIP_PUSH") == "1"

    try:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY 未配置")

        # 1. 拉截图
        screenshots = fetch_screenshots_last_week()
        if len(screenshots) < 3:
            raise RuntimeError(f"过去 7 天截图不足 3 张（实际 {len(screenshots)}），跳过生成")

        # 2. 选品
        pick = pick_product(screenshots)

        # 3. 生成报告
        report = generate_report(pick)

        # 4. 存截图
        date_str = report["dateOf"]
        shots_meta = save_screenshots(pick, date_str)

        # 5. 写 JSON
        report_path = write_report(report, shots_meta)

        # 6. Git push
        pushed = False
        if skip_push:
            log.info("SKIP_PUSH=1，跳过 git push（本地验证模式）")
        else:
            pushed = git_push()

        msg = f"✅ 产品深度报告已生成：{report['productName']} | {report['title']}"
        if pushed:
            msg += " | 已推送 GitHub Pages"
        elif skip_push:
            msg += " | 本地模式未推送"
        log.info(msg)
        wecom_notify(msg)
        return 0

    except Exception as e:
        err = f"❌ 产品深度报告生成失败: {e}\n{traceback.format_exc()}"
        log.error(err)
        wecom_notify(f"产品深度报告生成失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
