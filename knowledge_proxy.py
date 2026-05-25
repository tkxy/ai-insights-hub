#!/usr/bin/env python3
"""
Knowledge Base 本地抓取代理
- 抓取任意 URL 的网页内容（包括微信公众号）
- 用 DeepSeek API 做 AI 总结
- 返回 JSON 给前端
- 端口 8126
"""

import json
import re
import sys
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from html.parser import HTMLParser

import urllib.request
import urllib.error
import ssl

PORT = 8126

# 忽略 SSL 证书问题
ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE


class TextExtractor(HTMLParser):
    """从 HTML 中提取纯文本，跳过 script/style"""
    def __init__(self):
        super().__init__()
        self.texts = []
        self.skip = False
        self.skip_tags = {'script', 'style', 'nav', 'footer', 'header', 'aside'}

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.skip_tags:
            self.skip = True

    def handle_endtag(self, tag):
        if tag.lower() in self.skip_tags:
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            text = data.strip()
            if text:
                self.texts.append(text)

    def get_text(self):
        return ' '.join(self.texts)


def fetch_url(url):
    """用 urllib 抓取网页，模拟浏览器 UA"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/131.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as resp:
        content_type = resp.headers.get('Content-Type', '')
        # 尝试从 Content-Type 获取编码
        charset = 'utf-8'
        if 'charset=' in content_type:
            charset = content_type.split('charset=')[-1].strip().split(';')[0]
        raw = resp.read()
        # 尝试解码
        for enc in [charset, 'utf-8', 'gbk', 'gb2312', 'latin-1']:
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode('utf-8', errors='replace')


def extract_meta(html, attr_name, attr_value):
    """提取 meta 标签内容"""
    # property="og:title" content="..."
    pattern = rf'<meta[^>]*{attr_name}=["\']?{re.escape(attr_value)}["\']?[^>]*content=["\']([^"\']*)["\']'
    m = re.search(pattern, html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    # content 在前面的情况
    pattern2 = rf'<meta[^>]*content=["\']([^"\']*)["\'][^>]*{attr_name}=["\']?{re.escape(attr_value)}["\']?'
    m2 = re.search(pattern2, html, re.IGNORECASE)
    if m2:
        return m2.group(1).strip()
    return ''


def extract_title(html):
    """提取页面标题"""
    # 优先 og:title
    og = extract_meta(html, 'property', 'og:title')
    if og:
        return og
    # 微信公众号多种写法
    for pat in [
        r'var\s+msg_title\s*=\s*["\'](.+?)["\']',
        r'var\s+msg_title\s*=\s*"(.+?)"\.html\(',
        r'"msg_title"\s*:\s*"(.+?)"',
        r'class="rich_media_title"[^>]*>([^<]+)<',
    ]:
        m = re.search(pat, html, re.DOTALL)
        if m:
            t = m.group(1).strip()
            if t and len(t) > 2:
                return t
    # <title>
    m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ''


def extract_description(html):
    """提取页面描述"""
    og = extract_meta(html, 'property', 'og:description')
    if og:
        return og
    meta = extract_meta(html, 'name', 'description')
    if meta:
        return meta
    # 微信公众号多种写法
    for pat in [
        r'var\s+msg_desc\s*=\s*["\'](.+?)["\']',
        r'var\s+msg_desc\s*=\s*"(.+?)"\.html\(',
        r'"msg_desc"\s*:\s*"(.+?)"',
    ]:
        m = re.search(pat, html, re.DOTALL)
        if m:
            d = m.group(1).strip()
            if d and len(d) > 2:
                return d
    return ''


def extract_keywords(html):
    """提取关键词"""
    return extract_meta(html, 'name', 'keywords')


def extract_publish_time(html):
    """提取文章发布时间"""
    # 1. og:article:published_time / article:published_time
    for attr in ['article:published_time', 'og:article:published_time']:
        t = extract_meta(html, 'property', attr)
        if t:
            return t

    # 2. meta name="publish_time" / "publishdate" / "date"
    for name in ['publish_time', 'publishdate', 'date', 'PubDate']:
        t = extract_meta(html, 'name', name)
        if t:
            return t

    # 3. 微信公众号：var ct = "1712345678" 或 document.getElementById("publish_time")
    for pat in [
        r'var\s+ct\s*=\s*["\'](\d{10})["\']',
        r'"publish_time"\s*:\s*"([^"]+)"',
        r'id="publish_time"[^>]*>([^<]+)<',
        r'class="publish_time"[^>]*>([^<]+)<',
        r'"create_time"\s*:\s*(\d{10})',
    ]:
        m = re.search(pat, html)
        if m:
            val = m.group(1).strip()
            # 如果是 unix timestamp
            if val.isdigit() and len(val) == 10:
                from datetime import datetime, timezone, timedelta
                dt = datetime.fromtimestamp(int(val), tz=timezone(timedelta(hours=8)))
                return dt.strftime('%Y-%m-%d %H:%M')
            if val:
                return val

    # 4. time 标签
    m = re.search(r'<time[^>]*datetime=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if m:
        return m.group(1).strip()

    # 5. JSON-LD datePublished
    m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    if m:
        return m.group(1).strip()

    return ''


def extract_body_text(html):
    """提取正文文本"""
    # 微信公众号：多种容器 ID
    content_html = None
    for pattern in [
        r'id="js_content"[^>]*>(.*?)</div>\s*(?:<div|<script)',
        r'class="rich_media_content[^"]*"[^>]*>(.*?)</div>\s*(?:<div class="rich_media_tool|<script)',
        r'id="js_article"[^>]*>(.*?)</div>\s*(?:<div|<script)',
    ]:
        m = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if m and len(m.group(1)) > 50:
            content_html = m.group(1)
            break

    if not content_html:
        content_html = html

    extractor = TextExtractor()
    try:
        extractor.feed(content_html)
    except Exception:
        pass
    return extractor.get_text()


def clean_text(text):
    """清理 HTML 实体和转义字符"""
    import html as html_mod
    if not text:
        return ''
    # 处理 \xNN 转义
    text = re.sub(r'\\x([0-9a-fA-F]{2})', lambda m: chr(int(m.group(1), 16)), text)
    # 处理 &#NNN; 和 &amp; 等 HTML 实体
    text = html_mod.unescape(text)
    # 处理多余空白
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def get_deepseek_key():
    """从 macOS 钥匙串读取 DeepSeek API Key"""
    try:
        result = subprocess.run(
            ['security', 'find-generic-password', '-s', 'DeepSeek API Key', '-w'],
            capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip()
    except Exception:
        return ''


DEEPSEEK_KEY = get_deepseek_key()
DEEPSEEK_URL = 'https://api.deepseek.com/chat/completions'


def call_deepseek(prompt, max_tokens=800):
    """调用 DeepSeek API"""
    if not DEEPSEEK_KEY:
        return None

    body = json.dumps({
        'model': 'deepseek-chat',
        'messages': [
            {'role': 'system', 'content': '你是一个专业的内容分析助手。用中文回答。'},
            {'role': 'user', 'content': prompt}
        ],
        'max_tokens': max_tokens,
        'temperature': 0.3
    }).encode('utf-8')

    req = urllib.request.Request(DEEPSEEK_URL, data=body, headers={
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {DEEPSEEK_KEY}'
    })

    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data['choices'][0]['message']['content'].strip()
    except Exception as e:
        print(f'[DeepSeek error] {e}')
        return None


def ai_summarize(title, body_text, description):
    """用 DeepSeek 生成文章总结和标签"""
    content = body_text[:2500] if body_text else description[:500]
    if not content or len(content) < 20:
        return None

    prompt = f"""你是一个资深的内容分析师。请仔细阅读下面这篇文章，然后做一个结构化总结。

文章标题：{title}

文章正文（可能不完整）：
{content}

请严格按照以下 JSON 格式返回（不要用 markdown 代码块包裹）：
{{
  "summary": "这里写文章的结构化总结，要求如下：1）先用一句话概括全文核心论点；2）然后分 2-3 个要点展开，每个要点说清楚文章讲了什么具体内容、引用了什么案例或数据；3）总字数 150-250 字；4）不要说"本文讨论了"这种废话，直接写内容",
  "insight": "这里写一段对 AI/科技/产品从业者的启示（50-100字），要具体到可以怎么用、可以关注什么，不要泛泛而谈",
  "tags": ["标签1", "标签2", "标签3"]
}}

具体要求：
- summary 必须让没读过原文的人能知道文章到底讲了什么，而不只是"这篇文章讲了某某话题"
- 如果文章引用了具体的产品名、公司名、数据或案例，summary 里要体现
- insight 要具体可行，避免"值得关注""意义重大"这类空话
- tags 给 3 个最精准的中文标签，优先用具体概念而不是大类
- 直接返回纯 JSON，不要任何额外文字"""

    result = call_deepseek(prompt, max_tokens=1000)
    if not result:
        return None

    # 清理可能的 markdown 代码块
    result = re.sub(r'^```(?:json)?\s*', '', result.strip())
    result = re.sub(r'\s*```$', '', result.strip())

    try:
        return json.loads(result)
    except json.JSONDecodeError:
        # 尝试提取 JSON 部分
        m = re.search(r'\{.*\}', result, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        return None


def analyze_url(url):
    """抓取并分析 URL"""
    html = fetch_url(url)

    title = clean_text(extract_title(html))
    description = clean_text(extract_description(html))
    keywords = clean_text(extract_keywords(html))
    body_text = clean_text(extract_body_text(html))
    publish_time = clean_text(extract_publish_time(html))

    # 用 AI 总结
    ai_result = ai_summarize(title, body_text, description)

    if ai_result:
        summary = ai_result.get('summary', description or body_text[:300])
        insight = ai_result.get('insight', '')
        tags = ai_result.get('tags', [])
    else:
        summary = description if description else (body_text[:300] + '...' if len(body_text) > 300 else body_text)
        insight = ''
        tags = []

    # 补充标签
    if keywords and not tags:
        tags = [t.strip() for t in re.split(r'[,，、;；]', keywords) if t.strip()][:3]

    return {
        'title': title[:200] if title else '',
        'summary': summary[:500] if summary else '',
        'insight': insight[:300] if insight else '',
        'tags': tags[:4] if tags else [],
        'publish_time': publish_time[:50] if publish_time else '',
        'description': description[:500] if description else '',
        'keywords': keywords[:200] if keywords else '',
        'body_text': body_text[:3000] if body_text else '',
        'success': True
    }


class ProxyHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        """GET /analyze?url=... """
        parsed = urlparse(self.path)
        if parsed.path == '/health':
            self.send_response(200)
            self._cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': True}).encode())
            return

        if parsed.path != '/analyze':
            self.send_response(404)
            self._cors_headers()
            self.end_headers()
            return

        params = parse_qs(parsed.query)
        url = params.get('url', [''])[0]
        if not url:
            self._json_error(400, '缺少 url 参数')
            return

        url = unquote(url)
        try:
            result = analyze_url(url)
            self._json_response(200, result)
        except Exception as e:
            self._json_error(500, f'抓取失败: {str(e)}')

    def _cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json_response(self, code, data):
        self.send_response(code)
        self._cors_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _json_error(self, code, msg):
        self._json_response(code, {'success': False, 'error': msg})

    def log_message(self, format, *args):
        print(f"[knowledge_proxy] {args[0]}")


if __name__ == '__main__':
    server = HTTPServer(('127.0.0.1', PORT), ProxyHandler)
    print(f'Knowledge proxy running on http://127.0.0.1:{PORT}')
    print(f'  GET /analyze?url=<encoded_url>')
    print(f'  GET /health')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')
