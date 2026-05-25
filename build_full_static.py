#!/usr/bin/env python3
"""Build a fully static HTML page with Insight + Map tabs.
All data is pre-rendered into HTML. Tab switching uses minimal inline JS.
No fetch, no async, no await."""

import json
from pathlib import Path
from build_march_map import build_map_data, PRIORITY_WEIGHT

BASE = Path(__file__).resolve().parent
DATA = BASE / 'data'


def load_latest_daily():
    """Load the most recent daily JSON."""
    files = sorted(DATA.glob('2026-03-*.json'), reverse=True)
    for f in files:
        if 'demand-map' in f.name:
            continue
        payload = json.loads(f.read_text('utf-8'))
        if payload.get('design_insights'):
            return payload
    return None


def esc(text):
    if not text:
        return ''
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def trunc(text, n=120):
    if not text:
        return ''
    t = esc(text)
    return t[:n].rstrip() + '…' if len(t) > n else t


def render_insight(data):
    if not data:
        return '<div style="padding:60px;text-align:center;color:#888;">暂无日报数据</div>'

    html = []
    # Hero
    html.append(f'''
    <div style="margin-bottom:40px;">
      <h1 style="font-size:clamp(28px,5vw,48px);font-weight:900;letter-spacing:-2px;line-height:1.1;margin-bottom:20px;">Today's AI Design Insights.</h1>
      <p style="color:#555;font-size:15px;line-height:1.8;max-width:900px;">{esc(data["daily_summary"])}</p>
      <div style="display:flex;gap:40px;margin-top:24px;">
        <div><div style="font-size:36px;font-weight:900;">{len(data["design_insights"])}</div><div style="font-size:13px;color:#888;">设计洞察</div></div>
        <div><div style="font-size:36px;font-weight:900;">{len(data["demand_mining"])}</div><div style="font-size:13px;color:#888;">需求挖掘</div></div>
        <div><div style="font-size:36px;font-weight:900;">{len(data["hot_news"])}</div><div style="font-size:13px;color:#888;">热点资讯</div></div>
      </div>
    </div>''')

    # Insights
    html.append('<h2 style="font-size:24px;font-weight:800;margin-bottom:20px;">设计洞察</h2>')
    html.append('<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px;margin-bottom:40px;">')
    for i, ins in enumerate(data['design_insights']):
        sources_html = ''
        if ins.get('source_urls'):
            sources_html = ''.join(f'<a href="{s["url"]}" target="_blank" style="display:inline-block;margin-right:8px;margin-top:8px;padding:6px 10px;border-radius:10px;background:rgba(0,0,0,0.04);color:inherit;text-decoration:none;font-size:12px;">{esc(s["name"])}</a>' for s in ins['source_urls'])
        html.append(f'''
        <div style="background:#fff;border-radius:20px;padding:24px;border:1px solid rgba(0,0,0,0.06);">
          <div style="font-size:11px;color:#888;font-weight:700;margin-bottom:12px;">洞察 #{i+1}</div>
          <h3 style="font-size:18px;font-weight:800;margin-bottom:12px;">{esc(ins["title"])}</h3>
          <p style="font-size:14px;color:#555;line-height:1.8;margin-bottom:12px;">{esc(ins["summary"])}</p>
          <div style="padding:14px;border-radius:14px;background:rgba(0,0,0,0.03);margin-bottom:8px;">
            <div style="font-size:11px;font-weight:700;color:#888;margin-bottom:6px;">对输入法的启示</div>
            <p style="font-size:13px;color:#555;line-height:1.7;">{esc(ins["implication"])}</p>
          </div>
          {sources_html}
        </div>''')
    html.append('</div>')

    # Demands
    html.append('<h2 style="font-size:24px;font-weight:800;margin-bottom:20px;">需求挖掘</h2>')
    html.append('<div style="display:grid;gap:16px;margin-bottom:40px;">')
    for dm in data['demand_mining']:
        p_color = '#d44' if dm['priority'] == '高' else ('#c87b1a' if dm['priority'] == '中' else '#5f7d42')
        html.append(f'''
        <div style="background:#fff;border-radius:20px;padding:24px;border:1px solid rgba(0,0,0,0.06);">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <h3 style="font-size:18px;font-weight:800;">{esc(dm["title"])}</h3>
            <span style="padding:6px 12px;border-radius:999px;font-size:12px;font-weight:700;color:{p_color};background:{p_color}18;">优先级：{dm["priority"]}</span>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;">
            <div><div style="font-size:11px;font-weight:700;color:#888;margin-bottom:6px;">痛点</div><p style="font-size:13px;color:#555;line-height:1.7;">{trunc(dm["pain_point"], 200)}</p></div>
            <div><div style="font-size:11px;font-weight:700;color:#888;margin-bottom:6px;">机会点</div><p style="font-size:13px;color:#555;line-height:1.7;">{trunc(dm["opportunity"], 200)}</p></div>
          </div>
        </div>''')
    html.append('</div>')

    # News
    html.append('<h2 style="font-size:24px;font-weight:800;margin-bottom:20px;">热点资讯</h2>')
    html.append('<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;">')
    for n in data['hot_news']:
        html.append(f'''
        <div style="background:#fff;border-radius:18px;padding:20px;border:1px solid rgba(0,0,0,0.06);">
          <div style="display:flex;justify-content:space-between;margin-bottom:10px;">
            <span style="font-size:11px;padding:4px 12px;border-radius:100px;background:rgba(0,0,0,0.05);color:#555;font-weight:600;">{esc(n["category"])}</span>
            <span style="font-size:11px;font-weight:600;color:{"#d44" if n["relevance"]=="高" else "#c87b1a"};">相关度：{n["relevance"]}</span>
          </div>
          <h3 style="font-size:16px;font-weight:700;margin-bottom:8px;">{esc(n["title"])}</h3>
          <p style="font-size:13px;color:#555;line-height:1.7;">{trunc(n["summary"], 160)}</p>
          <div style="margin-top:12px;padding-top:12px;border-top:1px solid rgba(0,0,0,0.06);font-size:12px;color:#888;display:flex;justify-content:space-between;">
            <span>{esc(n["source"])}</span>
            <a href="{n["source_url"]}" target="_blank" style="color:#111;text-decoration:none;font-weight:600;">阅读原文</a>
          </div>
        </div>''')
    html.append('</div>')

    return '\n'.join(html)


def render_map(map_data):
    if not map_data or map_data['totalItems'] == 0:
        return '<div style="padding:60px;text-align:center;color:#888;">暂无可聚合的需求挖掘数据</div>'

    pc = map_data.get('priorityCounts', {})
    html = []

    # Hero
    chips = ''.join(f'<span style="padding:8px 14px;border-radius:999px;background:rgba(0,0,0,0.05);color:#555;font-size:13px;font-weight:600;">{c["label"]} · {c["itemCount"]}</span>' for c in map_data['clusters'])

    highlights = ''.join(f'<div style="padding:14px 16px;border-radius:16px;background:rgba(0,0,0,0.04);color:#555;font-size:14px;line-height:1.7;">{esc(h)}</div>' for h in map_data.get('highlights', []))

    html.append(f'''
    <div style="background:linear-gradient(135deg,rgba(255,255,255,0.98),rgba(255,248,236,0.92));border:1px solid rgba(0,0,0,0.06);border-radius:28px;padding:36px;display:grid;grid-template-columns:minmax(0,1.25fr) minmax(300px,0.95fr);gap:28px;margin-bottom:28px;">
      <div>
        <div style="font-size:12px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;color:#888;margin-bottom:16px;">Monthly demand map</div>
        <h1 style="font-size:clamp(30px,5vw,52px);font-weight:900;letter-spacing:-0.04em;line-height:0.98;margin-bottom:14px;">{map_data["monthLabel"]} Demand Map.</h1>
        <p style="color:#555;font-size:15px;line-height:1.8;max-width:800px;">以月为维度展示当月的需求挖掘，按功能聚类展开。你可以快速看出这个月的机会更偏语音、帮写、打字效率，还是安全信任与服务入口。</p>
        <div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:20px;">{chips}</div>
        <div style="display:grid;gap:10px;margin-top:20px;">{highlights}</div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;align-content:start;">
        <div style="background:#fff;border-radius:24px;padding:22px;border:1px solid rgba(0,0,0,0.04);">
          <div style="font-size:40px;font-weight:800;letter-spacing:-0.05em;">{map_data["totalItems"]}</div>
          <div style="font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#888;margin-top:10px;">本月需求总数</div>
        </div>
        <div style="background:#fff;border-radius:24px;padding:22px;border:1px solid rgba(0,0,0,0.04);">
          <div style="font-size:40px;font-weight:800;letter-spacing:-0.05em;">{map_data["highPriorityCount"]}</div>
          <div style="font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#888;margin-top:10px;">高优机会数</div>
        </div>
        <div style="background:#fff;border-radius:24px;padding:22px;border:1px solid rgba(0,0,0,0.04);">
          <div style="font-size:40px;font-weight:800;letter-spacing:-0.05em;">{map_data["clusterCount"]}</div>
          <div style="font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#888;margin-top:10px;">功能聚类数</div>
        </div>
        <div style="background:#fff;border-radius:24px;padding:22px;border:1px solid rgba(0,0,0,0.04);">
          <div style="font-size:40px;font-weight:800;letter-spacing:-0.05em;">{pc.get("高",0)}/{pc.get("中",0)}/{pc.get("低",0)}</div>
          <div style="font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;color:#888;margin-top:10px;">高/中/低</div>
        </div>
      </div>
    </div>''')

    # Clusters
    for cluster in map_data['clusters']:
        fd = cluster.get('earliestDate', '')
        ld = cluster.get('latestDate', '')
        cards = []
        for item in cluster['items']:
            p_color = '#d44' if item['priority'] == '高' else ('#c87b1a' if item['priority'] == '中' else '#5f7d42')
            tags_html = ''.join(f'<span style="padding:6px 10px;border-radius:999px;background:rgba(0,0,0,0.05);color:#555;font-size:12px;font-weight:700;">{esc(t)}</span>' for t in item.get('tags', []))
            cards.append(f'''
            <div style="background:#fcfbf8;border-radius:20px;border:1px solid rgba(0,0,0,0.06);padding:18px;display:grid;gap:12px;">
              <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-size:12px;font-weight:700;color:#888;">{item["date"][5:]}</span>
                <span style="padding:6px 10px;border-radius:999px;font-size:12px;font-weight:700;color:{p_color};background:{p_color}18;">优先级：{item["priority"]}</span>
              </div>
              <h4 style="font-size:17px;font-weight:800;line-height:1.35;">{esc(item["title"])}</h4>
              <div style="color:#555;font-size:13px;line-height:1.7;"><strong style="display:block;font-size:11px;color:#888;letter-spacing:1px;margin-bottom:4px;">机会点</strong>{trunc(item.get("opportunity",""), 160)}</div>
              <div style="color:#555;font-size:13px;line-height:1.7;"><strong style="display:block;font-size:11px;color:#888;letter-spacing:1px;margin-bottom:4px;">为什么现在</strong>{trunc(item.get("evidence",""), 120)}</div>
              <div style="display:flex;flex-wrap:wrap;gap:8px;">{tags_html}</div>
            </div>''')

        html.append(f'''
        <div style="background:rgba(255,255,255,0.88);border-radius:24px;border:1px solid rgba(0,0,0,0.06);padding:28px;margin-bottom:18px;">
          <div style="display:flex;justify-content:space-between;gap:18px;align-items:start;margin-bottom:20px;flex-wrap:wrap;">
            <div>
              <div style="font-size:11px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;color:#888;margin-bottom:10px;">Function cluster</div>
              <h3 style="font-size:28px;font-weight:900;letter-spacing:-1px;margin-bottom:8px;">{esc(cluster["label"])}</h3>
              <p style="font-size:14px;color:#555;line-height:1.8;">{esc(cluster["description"])}</p>
            </div>
            <div style="display:flex;gap:10px;flex-wrap:wrap;">
              <div style="background:rgba(0,0,0,0.05);padding:12px 14px;border-radius:18px;min-width:100px;">
                <div style="font-size:22px;font-weight:800;">{cluster["itemCount"]}</div>
                <div style="font-size:11px;font-weight:700;color:#888;margin-top:4px;">累计机会</div>
              </div>
              <div style="background:rgba(0,0,0,0.05);padding:12px 14px;border-radius:18px;min-width:100px;">
                <div style="font-size:22px;font-weight:800;">{cluster["activeDays"]}</div>
                <div style="font-size:11px;font-weight:700;color:#888;margin-top:4px;">覆盖日期</div>
              </div>
              <div style="background:rgba(0,0,0,0.05);padding:12px 14px;border-radius:18px;min-width:100px;">
                <div style="font-size:22px;font-weight:800;">{cluster["highPriorityCount"]}</div>
                <div style="font-size:11px;font-weight:700;color:#888;margin-top:4px;">高优需求</div>
              </div>
            </div>
          </div>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;">
            {"".join(cards)}
          </div>
        </div>''')

    return '\n'.join(html)


def build():
    daily = load_latest_daily()
    map_data = build_map_data()

    date_display = '3月31日 周二'
    insight_html = render_insight(daily)
    map_html = render_map(map_data)

    page = f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AI Insights Hub</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#ededea;color:#111;line-height:1.55;min-height:100vh;}}
.header{{padding:0 48px;height:72px;display:flex;align-items:center;justify-content:space-between;max-width:1400px;margin:0 auto;}}
.logo{{display:flex;align-items:center;gap:20px;}}
.logo-mark{{font-size:28px;font-weight:900;letter-spacing:-1.5px;}}
.nav{{display:flex;gap:6px;padding:6px;background:rgba(255,255,255,0.55);border:1px solid #ddd;border-radius:999px;}}
.tab-btn{{font-size:14px;font-weight:600;color:#555;cursor:pointer;border:none;background:transparent;padding:10px 18px;border-radius:999px;font-family:inherit;}}
.tab-btn:hover{{color:#111;background:rgba(0,0,0,0.04);}}
.tab-btn.active{{background:#111;color:#fff;}}
.wrap{{max-width:1400px;margin:0 auto;padding:20px 48px 60px;}}
.page{{display:none;}}
.page.active{{display:block;}}
.footer{{max-width:1400px;margin:0 auto;padding:24px 48px 48px;text-align:center;color:#aaa;font-size:12px;}}
@media(max-width:900px){{
  .header{{padding:0 20px;}}
  .wrap{{padding:16px 20px 40px;}}
}}
</style>
</head>
<body>

<div class="header">
  <div class="logo">
    <div class="logo-mark">A.</div>
    <nav class="nav">
      <button class="tab-btn active" onclick="switchTab('insight')">Insight</button>
      <button class="tab-btn" onclick="switchTab('map')">Map</button>
    </nav>
  </div>
  <div style="font-size:13px;font-weight:600;color:#111;">{date_display}</div>
</div>

<div class="wrap">
  <div class="page active" id="page-insight">
    {insight_html}
  </div>
  <div class="page" id="page-map">
    {map_html}
  </div>
</div>

<div class="footer">AI Insights Hub · 每日 8:00 自动更新</div>

<script>
function switchTab(id) {{
  document.querySelectorAll('.page').forEach(function(p) {{ p.classList.remove('active'); }});
  document.querySelectorAll('.tab-btn').forEach(function(b) {{ b.classList.remove('active'); }});
  var page = document.getElementById('page-' + id);
  if (page) page.classList.add('active');
  var btns = document.querySelectorAll('.tab-btn');
  for (var i = 0; i < btns.length; i++) {{
    if (btns[i].textContent.toLowerCase().indexOf(id) >= 0) btns[i].classList.add('active');
  }}
  window.scrollTo(0, 0);
}}
if (window.location.hash === '#map') switchTab('map');
</script>
</body>
</html>'''

    out = BASE / 'full.html'
    out.write_text(page, encoding='utf-8')
    print(f'Wrote {out} ({out.stat().st_size / 1024:.1f} KB)')


if __name__ == '__main__':
    build()
