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
import base64
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
        try:
            cand = resp.get("candidates", [{}])[0]
            log.warning(f"extract_text 失败，candidate finishReason={cand.get('finishReason')}, 内容 parts={cand.get('content',{}).get('parts')}, promptFeedback={resp.get('promptFeedback')}")
        except Exception:
            log.warning(f"extract_text 彻底失败，resp keys={list(resp.keys()) if isinstance(resp, dict) else type(resp)}")
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
def slugify(name):
    # 优先保留英文/数字部分
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    if s:
        return s
    # 全中文或无 ASCII：用 md5 前 6 位做稳定短 slug
    import hashlib
    return "p-" + hashlib.md5(name.encode("utf-8")).hexdigest()[:6]


def _aggregate_by_competitor(screenshots):
    """按产品名聚合截图，过滤模糊分组，返回 {name: [shots]}"""
    VAGUE = {"其他", "未知", "未分类", "other", "unknown", ""}
    agg = {}
    for s in screenshots:
        name = (s.get("competitorName") or s.get("competitor") or "").strip()
        if name in VAGUE:
            text_blob = (s.get("title", "") + " " + s.get("summary", "") + " " + " ".join(s.get("tags", []))).lower()
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
    return agg


def pick_products(screenshots, min_count=2, max_products=8):
    """返回所有 ≥min_count 张截图的明确竞品列表（按截图数倒序），各自让 Gemini 取一句 focusAngle"""
    agg = _aggregate_by_competitor(screenshots)
    if not agg:
        raise RuntimeError("本周截图中未能识别出任何明确产品（全部为模糊分组）")

    # 按截图数倒序，筛 ≥min_count
    items = sorted(agg.items(), key=lambda kv: -len(kv[1]))
    picks = []
    for name, shots in items[:max_products]:
        if len(shots) < min_count:
            continue
        reason = f"本周共 {len(shots)} 张截图，关注度较高"
        focus_titles = [s.get("title", "") for s in shots[:3] if s.get("title")]
        focus_angle = "交互创新 / 视觉动效" if not focus_titles else "；".join(focus_titles[:2])[:30]
        picks.append({
            "productName": name,
            "productSlug": slugify(name),
            "reason": reason,
            "focusAngle": focus_angle,
            "screenshots": shots[:12],
        })
    if not picks:
        raise RuntimeError(f"本周没有任何竞品达到最少 {min_count} 张截图门槛")
    log.info(f"本周待报道竞品（共 {len(picks)}）：" + " | ".join(f"{p['productName']}({len(p['screenshots'])}张)" for p in picks))
    return picks


def pick_product(screenshots):
    """单选版（保留兼容，返回截图量最多的那个）"""
    return pick_products(screenshots, min_count=1, max_products=1)[0]


# ---------------------- Step 2: 联网搜料 + 生成报告 ----------------------
def _load_screenshot_as_inline(s):
    """把一张截图下载成 base64 inlineData，失败返回 None"""
    src_url = s.get("url", "")
    if not src_url:
        return None
    if src_url.startswith("/"):
        src_url = H5_API_BASE + src_url
    try:
        raw = http_get_bytes(src_url)
    except Exception as e:
        log.warning(f"图片下载失败 {src_url}: {e}")
        return None
    # 从扩展名粗判 MIME，默认 jpeg
    ext = (Path(s.get("filename") or src_url).suffix or ".jpg").lower().lstrip(".")
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/jpeg")
    return {
        "inlineData": {
            "mimeType": mime,
            "data": base64.b64encode(raw).decode("ascii"),
        }
    }


def generate_report(pick):
    """用 Gemini multimodal 直接看截图 + Google Search 补公开资料"""
    product = pick["productName"]
    angle = pick.get("focusAngle", "")

    # === Step A: 把截图图片本体（最多 8 张）准备成 inlineData ===
    #  太多会撑爆 token（每张 ~300KB base64），8 张通常够讲清楚了
    shots = pick["screenshots"][:8]
    image_parts = []
    shot_index_notes = []  # 给 prompt 里的"图 #N = XX"注脚
    for i, s in enumerate(shots, 1):
        part = _load_screenshot_as_inline(s)
        if not part:
            continue
        image_parts.append(part)
        # 给每张图配一个编号注脚（标题/分类），让模型能引用"图#N"
        note = f"图 #{i}"
        if s.get("title"):
            note += f"｜{s['title']}"
        if s.get("categoryName"):
            note += f"（{s['categoryName']}）"
        shot_index_notes.append(note)

    if not image_parts:
        raise RuntimeError(f"{product} 没有成功加载任何截图，无法生成报告")

    log.info(f"{product} 直连 Gemini {len(image_parts)} 张截图做 multimodal 分析")

    # === Step B: 反 AI 腔 system 指令 ===
    system_inst = """你是一个挑剔、写过 10 年产品手记的竞品观察员，帮腾讯搜狗输入法团队读图。

你的铁律（违反任何一条视为不合格）：
1. 只写你在截图里**实际看到**的东西。画面没有的不要编。推测、脑补、"这体现了"式的玄学一律不写。
2. 禁止使用下列词/句式：赋能、打造、构建、深化、无缝、生态、范式、护城河、升级体验、智能化、数字化、价值链、一体化、管家、金鱼、真正做到、实现了 XX 的可能性、为用户提供 XX 价值。
3. 禁止写"可以借鉴"、"可以探索"、"可以考虑"、"建议加强 XX"这种没承诺的废话。要写就写"搜狗输入法键盘右上角工具栏，在第 3 个位置增加 XX 按钮"这种级别。
4. 不要排比、不要三段式套话（"传统 XX 是…Grammarly 通过…从而…"）。就用短句和白话。
5. observation 字段要像目击证人作证：画面左上角是什么、按钮上写了什么字、弹窗出现时覆盖了哪部分界面、配色是什么。用像素和文字说话。
6. sogouTakeaway 字段必须同时含三要素：【哪个场景】+【搜狗输入法的哪个具体位置 / 模块】+【用户做什么动作会发生什么】。示例："在微信聊天输入时，搜狗候选词栏上方新增一条横行，用户选中自己刚输入的这段文字后，这条横行展示 3 个一键改写选项（更礼貌/更简洁/更有梗）"。
7. 不要写思考过程、不要自言自语、不要输出 tool_code，直接给 JSON 结果。

每句话问自己：如果去掉这句，读者会少什么具体信息？如果少不了什么，就删掉。"""

    shot_legend = "\n".join(shot_index_notes)

    prompt = f"""你会看到 {len(image_parts)} 张「{product}」的产品截图。
重点方向：{angle or "（无，自己判断）"}

截图编号参照：
{shot_legend}

仔细看图，然后**直接输出 JSON**（不要思考过程、不要 tool_code、不要前言、不要 markdown 围栏）：
{{
  "productName": "{product}",
  "productSlug": "{pick['productSlug']}",
  "title": "10-18字标题，要有画面感，不要用'深度解读/全面解析'这类词",
  "subtitle": "20-30字，点出这个产品最不一样的那一点",
  "overview": {{
    "what": "一句话，它是干嘛的，说人话",
    "scene": "截图里出现的具体使用场景，3-5个词",
    "audience": "看截图能推断的目标用户，一句话"
  }},
  "innovations": [
    {{
      "title": "5-10字，名词短语，别用'XX 之道/XX 魔法'这类修辞",
      "icon": "一个 emoji",
      "screenshotRef": "图 #N（必填，指明这一条讲的是哪张或哪几张图）",
      "observation": "照着截图讲：画面里有什么按钮/弹窗/文案/布局，位置在哪，写了什么字。60-120 字，只写看得见的。",
      "sogouTakeaway": "按铁律第 6 条的三要素格式写，80-140 字。不含场景+位置+动作的直接扣掉。"
    }}
  ],
  "visualMotion": {{
    "summary": "20字内，就事论事说视觉风格（如'大留白+单色强调+无阴影'）",
    "highlights": ["截图里看到的具体视觉特征1", "特征2", "特征3"]
  }},
  "differentiation": {{
    "summary": "30字内，和同类产品比，它少做了什么、多做了什么",
    "positioning": "一句话市场定位"
  }},
  "sogouTakeaways": [
    "给搜狗输入法的具体建议 1：场景+位置+动作三要素，一句就是一个改造工单。不要复述 innovations 里的 takeaway。",
    "具体建议 2",
    "具体建议 3"
  ]
}}

innovations 写 3-5 条，sogouTakeaways 写 3-5 条。整体 **说人话 > 全面**，宁缺毋滥。

再次提醒：整个响应 = 一个合法 JSON 对象，以 {{ 开头，以 }} 结尾，不要任何其他文本。"""

    # Stage A: 只看图 + 出 JSON（不开 grounding，避免 CoT 污染输出）
    parts = [{"text": prompt}] + image_parts
    contents = [{"parts": parts}]
    resp = gemini_call(GEMINI_MODEL, contents, tools=None, system=system_inst, timeout=300, max_tokens=8192)
    text = extract_text(resp)
    sources = []

    try:
        report = extract_json_block(text)
    except Exception as e:
        log.error(f"报告 JSON 解析失败（前 400 字符）: {text[:400]}")
        raise RuntimeError(f"报告生成失败: {e}")

    # Stage B: grounding 小任务，只补 team / launchDate / 官方 sources
    # 失败不影响主报告
    try:
        fact_prompt = f"""用 Google 搜索「{product}」这个产品，找出以下两个事实信息并以 JSON 返回：
1. 开发团队 / 公司名（team）
2. 首发时间或最新版本时间（launchDate，精确到月或年份）

**直接输出 JSON，不要思考过程、不要 tool_code、不要 markdown 围栏**：
{{
  "team": "公司/团队名",
  "launchDate": "2024-10 或 2024 年 10 月"
}}"""
        fact_contents = [{"parts": [{"text": fact_prompt}]}]
        fact_resp = gemini_call(
            GEMINI_MODEL, fact_contents,
            tools=[{"googleSearch": {}}],
            system="你是事实核查员，只用 Google 查证事实，直接输出 JSON，不要废话。",
            timeout=90, max_tokens=1024,
        )
        fact_text = extract_text(fact_resp)
        try:
            fact = extract_json_block(fact_text)
            ov = report.setdefault("overview", {})
            if fact.get("team"):
                ov["team"] = fact["team"]
            if fact.get("launchDate"):
                ov["launchDate"] = fact["launchDate"]
        except Exception as je:
            log.warning(f"{product} 事实查证 JSON 解析失败，跳过: {je}；原文前 200: {fact_text[:200]}")
        sources = extract_grounding(fact_resp)
    except Exception as e:
        log.warning(f"{product} Stage B 事实查证失败（不影响主报告）: {e}")

    # 注入元数据
    today = datetime.now()
    report["generatedAt"] = today.strftime("%Y-%m-%d %H:%M")
    report["dateOf"] = today.strftime("%Y-%m-%d")
    report["reportType"] = "deep-dive"
    report["selectionReason"] = pick.get("reason", "")
    report["sources"] = sources
    return report


# ---------------------- Step 3: 落图 + 落盘 ----------------------
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

    # 亮点标题抽取（前 3 条 innovation.title，用于卡片外露）
    innovations = report.get("innovations", []) or []
    highlights = []
    for it in innovations[:3]:
        t = (it.get("title") or "").strip()
        if t:
            highlights.append(t)

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
        "innovationCount": len(innovations),
        "highlights": highlights,
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
    # MIN_SHOTS：最少截图门槛（默认 2，避免只 1 张就生成）
    min_shots = int(os.environ.get("MIN_SHOTS") or "2")
    # MAX_PRODUCTS：一次最多跑几个竞品（防炸 quota）
    max_products = int(os.environ.get("MAX_PRODUCTS") or "8")

    try:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY 未配置")

        # 1. 拉截图
        screenshots = fetch_screenshots_last_week()
        if len(screenshots) < 3:
            raise RuntimeError(f"过去 7 天截图不足 3 张（实际 {len(screenshots)}），跳过生成")

        # 2. 挑出所有符合门槛的竞品
        picks = pick_products(screenshots, min_count=min_shots, max_products=max_products)

        # ONLY_PRODUCT=xxx 只跑匹配产品（验证用）
        only = (os.environ.get("ONLY_PRODUCT") or "").strip().lower()
        if only:
            picks = [p for p in picks if only in (p.get("productName","") or "").lower() or only in (p.get("productSlug","") or "").lower()]
            log.info(f"ONLY_PRODUCT={only} 命中 {len(picks)} 个：" + " | ".join(p["productName"] for p in picks))
            if not picks:
                raise RuntimeError(f"ONLY_PRODUCT={only} 没有匹配任何竞品")

        # 3. 逐个生成报告
        succeeded = []
        failed = []
        for idx, pick in enumerate(picks, 1):
            pname = pick["productName"]
            log.info(f"\n{'-' * 40}\n[{idx}/{len(picks)}] 生成报告：{pname}\n{'-' * 40}")
            try:
                report = generate_report(pick)
                date_str = report["dateOf"]
                shots_meta = save_screenshots(pick, date_str)
                write_report(report, shots_meta)
                succeeded.append(f"{pname}（{len(shots_meta)} 图）")
            except Exception as e:
                log.error(f"竞品 {pname} 报告失败: {e}")
                failed.append(f"{pname}: {e}")
                continue

        if not succeeded:
            raise RuntimeError(f"所有竞品报告均失败：{failed}")

        # 4. 一次性 Git push
        pushed = False
        if skip_push:
            log.info("SKIP_PUSH=1，跳过 git push（本地验证模式）")
        else:
            pushed = git_push()

        summary = f"✅ 产品深度报告本周共生成 {len(succeeded)} 份：" + "，".join(succeeded)
        if failed:
            summary += f"\n⚠️ 失败 {len(failed)} 份：" + "；".join(failed)
        if pushed:
            summary += " | 已推送 GitHub Pages"
        elif skip_push:
            summary += " | 本地模式未推送"
        log.info(summary)
        wecom_notify(summary)
        return 0

    except Exception as e:
        err = f"❌ 产品深度报告生成失败: {e}\n{traceback.format_exc()}"
        log.error(err)
        wecom_notify(f"产品深度报告生成失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
