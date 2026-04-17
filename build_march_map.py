#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / 'data'
OUTPUT_FILE = BASE_DIR / 'march-map.html'
MONTH_KEY = '2026-03'
MONTH_LABEL = '2026年3月'

DEMAND_CLUSTERS = [
    {
        'key': 'voice',
        'label': '语音类',
        'description': '围绕语音输入、语音生成和口述驱动的表达流程。',
        'keywords': ['语音', '声音', '口述', '转语音', '朗读', 'voice'],
    },
    {
        'key': 'writing',
        'label': '帮写类',
        'description': '聚焦帮写生成、风格控制、素材调用和写作协同。',
        'keywords': ['帮写', '写作', '文案', '创作', '风格', '素材', '人设', '语气', '原创度', '多变体', '模型路由', '规则'],
    },
    {
        'key': 'typing',
        'label': '打字/输入类',
        'description': '强调输入框入口、回复效率、键盘操作和即时表达。',
        'keywords': ['输入框', '输入', '打字', '键盘', '预回复', '预测', '通知', '翻译', '零摩擦', '对话维度'],
    },
    {
        'key': 'visual',
        'label': '视觉/多模态类',
        'description': '结合图片、截图、拍照和屏幕上下文的混合输入能力。',
        'keywords': ['截图', '图片', '拍照', '视觉', '屏幕', '图文', '富内容', '可编辑文字'],
    },
    {
        'key': 'agent',
        'label': '服务/Agent类',
        'description': '把输入法延伸为服务入口、任务发起器和协作枢纽。',
        'keywords': ['agent', '智能体', '服务', '入口', '任务', '卡片', '跳转', '推荐', '代写', '超级入口'],
    },
    {
        'key': 'trust',
        'label': '安全/信任类',
        'description': '涉及安全、透明、合规、事实性与用户信任的底层能力。',
        'keywords': ['安全', '信任', '透明', '合规', '事实', '防伪', '回滚', '隐私', '可信'],
    },
    {
        'key': 'crossscreen',
        'label': '跨屏输入类',
        'description': '围绕多设备协同、PC-移动端联动、跨平台剪贴板和无缝切换的输入体验。',
        'keywords': ['跨屏', '跨端', '多设备', '多端', 'PC', '桌面', '手机', '平板', '剪贴板', '同步', '协同', '无缝切换', '接力', 'handoff', '跨平台'],
    },
]
OTHER_CLUSTER = {
    'key': 'other',
    'label': '其他探索',
    'description': '暂时没有被明确命中的功能线索，先放在观察区，后面可以继续细分。',
}
TAG_RULES = [
    {'tag': '语音', 'keywords': ['语音', '声音', '口述', '转语音']},
    {'tag': '帮写', 'keywords': ['帮写', '写作', '文案', '创作']},
    {'tag': '输入效率', 'keywords': ['输入框', '输入', '键盘', '打字', '预回复', '预测']},
    {'tag': '多模态', 'keywords': ['截图', '图片', '拍照', '屏幕', '视觉', '图文']},
    {'tag': '服务入口', 'keywords': ['服务', '智能体', 'agent', '入口', '卡片', '跳转', '任务']},
    {'tag': '安全信任', 'keywords': ['安全', '信任', '合规', '透明', '事实', '防伪']},
    {'tag': '跨屏输入', 'keywords': ['跨屏', '跨端', '多设备', '多端', 'PC', '桌面', '同步', '协同', '无缝切换', '接力', 'handoff', '跨平台']},
    {'tag': '个性化', 'keywords': ['人设', '语气', '风格', '规则']},
]
CLUSTER_META = {cluster['key']: cluster for cluster in [*DEMAND_CLUSTERS, OTHER_CLUSTER]}
PRIORITY_WEIGHT = {'高': 3, '中': 2, '低': 1}


def score_cluster(cluster: dict, title_text: str, full_text: str) -> int:
    score = 0
    for keyword in cluster['keywords']:
        normalized = keyword.lower()
        if normalized in title_text:
            score += 3
        elif normalized in full_text:
            score += 1
    return score


def classify_demand(item: dict) -> str:
    title_text = (item.get('title') or '').lower()
    full_text = ' '.join(filter(None, [
        item.get('title'),
        item.get('pain_point'),
        item.get('opportunity'),
        item.get('evidence'),
    ])).lower()

    best_key = OTHER_CLUSTER['key']
    best_score = 0
    for cluster in DEMAND_CLUSTERS:
        score = score_cluster(cluster, title_text, full_text)
        if score > best_score:
            best_score = score
            best_key = cluster['key']
    return best_key


def detect_tags(item: dict, cluster_key: str) -> list[str]:
    text = ' '.join(filter(None, [
        item.get('title'),
        item.get('pain_point'),
        item.get('opportunity'),
        item.get('evidence'),
    ])).lower()
    tags = [
        rule['tag']
        for rule in TAG_RULES
        if any(keyword.lower() in text for keyword in rule['keywords'])
    ]
    if not tags:
        tags.append(CLUSTER_META[cluster_key]['label'])
    return tags[:3]


def load_month_items() -> list[dict]:
    items: list[dict] = []
    for path in sorted(DATA_DIR.glob(f'{MONTH_KEY}-*.json')):
        payload = json.loads(path.read_text(encoding='utf-8'))
        date = path.stem
        for index, item in enumerate(payload.get('demand_mining', [])):
            cluster_key = classify_demand(item)
            items.append({
                'title': item.get('title', ''),
                'pain_point': item.get('pain_point', ''),
                'opportunity': item.get('opportunity', ''),
                'evidence': item.get('evidence', ''),
                'priority': item.get('priority', '中'),
                'source_urls': item.get('source_urls', []),
                'date': date,
                'index': index,
                'clusterKey': cluster_key,
                'tags': detect_tags(item, cluster_key),
            })
    return items


def build_map_data() -> dict:
    items = load_month_items()
    days = sorted({item['date'] for item in items})

    clusters = []
    for cluster in [*DEMAND_CLUSTERS, OTHER_CLUSTER]:
        cluster_items = [item for item in items if item['clusterKey'] == cluster['key']]
        if not cluster_items:
            continue
        cluster_items.sort(
            key=lambda item: (
                -PRIORITY_WEIGHT.get(item['priority'], 1),
                item['date'],
                item['index'],
            )
        )
        active_days = sorted({item['date'] for item in cluster_items})
        clusters.append({
            'key': cluster['key'],
            'label': cluster['label'],
            'description': cluster['description'],
            'items': cluster_items,
            'itemCount': len(cluster_items),
            'activeDays': len(active_days),
            'highPriorityCount': sum(1 for item in cluster_items if item['priority'] == '高'),
            'earliestDate': active_days[0],
            'latestDate': active_days[-1],
            'topTitles': [item['title'] for item in cluster_items[:3]],
        })

    clusters.sort(key=lambda cluster: (-cluster['itemCount'], -cluster['highPriorityCount'], cluster['label']))
    priority_counts = {
        '高': sum(1 for item in items if item['priority'] == '高'),
        '中': sum(1 for item in items if item['priority'] == '中'),
        '低': sum(1 for item in items if item['priority'] == '低'),
    }

    return {
        'monthKey': MONTH_KEY,
        'monthLabel': MONTH_LABEL,
        'generatedAt': '2026-03-31 19:20',
        'availableDays': len(days),
        'totalItems': len(items),
        'clusterCount': len(clusters),
        'highPriorityCount': priority_counts['高'],
        'priorityCounts': priority_counts,
        'firstDate': days[0],
        'lastDate': days[-1],
        'clusters': clusters,
        'highlights': [
            f'3 月共沉淀 {len(items)} 条需求，覆盖 {len(days)} 个有数据日期。',
            f'最密集的机会方向是 {clusters[0]["label"]}，累计 {clusters[0]["itemCount"]} 条。',
            f'高优机会共有 {priority_counts["高"]} 条，可以直接拿去做 4 月跟进池。',
        ],
    }


def build_html(map_data: dict) -> str:
    data_json = json.dumps(map_data, ensure_ascii=False)
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{MONTH_LABEL} Demand Map</title>
  <style>
    :root {{
      --bg: #f5f2eb;
      --panel: rgba(255,255,255,0.88);
      --panel-solid: #ffffff;
      --text: #111111;
      --text2: #4f4f4f;
      --text3: #7e7e7e;
      --line: rgba(17,17,17,0.08);
      --accent: #111111;
      --soft: #f3efe6;
      --high: #d64545;
      --mid: #d08a00;
      --low: #5f7d42;
      --shadow: 0 18px 60px rgba(17,17,17,0.08);
      --radius-xl: 32px;
      --radius-lg: 24px;
      --radius-md: 18px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(255,255,255,0.9), transparent 32%),
        linear-gradient(180deg, #f9f6f0 0%, #efe8db 100%);
      min-height: 100vh;
    }}
    .wrap {{ max-width: 1480px; margin: 0 auto; padding: 40px 24px 72px; }}
    .hero {{
      background: linear-gradient(135deg, rgba(255,255,255,0.98), rgba(255,248,236,0.92));
      border: 1px solid rgba(17,17,17,0.06);
      box-shadow: var(--shadow);
      border-radius: var(--radius-xl);
      padding: 36px;
      display: grid;
      grid-template-columns: minmax(0, 1.25fr) minmax(360px, 0.95fr);
      gap: 28px;
    }}
    .eyebrow {{
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 1.6px;
      text-transform: uppercase;
      color: var(--text3);
      margin-bottom: 16px;
    }}
    h1 {{ margin: 0 0 14px; font-size: clamp(34px, 5vw, 62px); line-height: 0.98; letter-spacing: -0.04em; }}
    .hero-desc {{ margin: 0; color: var(--text2); font-size: 16px; line-height: 1.9; max-width: 880px; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 20px; }}
    .meta-chip {{
      display: inline-flex;
      align-items: center;
      padding: 10px 14px;
      border-radius: 999px;
      background: rgba(17,17,17,0.05);
      color: var(--text2);
      font-size: 13px;
      font-weight: 600;
    }}
    .highlights {{ margin-top: 22px; display: grid; gap: 12px; }}
    .highlight {{
      padding: 16px 18px;
      border-radius: 18px;
      background: rgba(17,17,17,0.04);
      color: var(--text2);
      line-height: 1.7;
      font-size: 14px;
    }}
    .summary-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }}
    .summary-card {{
      border-radius: 24px;
      background: var(--panel-solid);
      padding: 22px 20px;
      border: 1px solid rgba(17,17,17,0.05);
      box-shadow: 0 10px 32px rgba(17,17,17,0.05);
    }}
    .summary-value {{ font-size: 40px; font-weight: 800; letter-spacing: -0.05em; line-height: 1; }}
    .summary-label {{ margin-top: 10px; font-size: 12px; text-transform: uppercase; letter-spacing: 1.2px; color: var(--text3); font-weight: 700; }}
    .summary-note {{ margin-top: 10px; color: var(--text2); font-size: 13px; line-height: 1.7; }}
    .toolbar {{ margin: 28px 0 18px; display: flex; justify-content: space-between; gap: 16px; align-items: center; flex-wrap: wrap; }}
    .toolbar-title {{ font-size: 24px; font-weight: 800; letter-spacing: -0.03em; }}
    .legend {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    .legend-pill {{ padding: 9px 12px; border-radius: 999px; background: rgba(17,17,17,0.05); color: var(--text2); font-size: 13px; font-weight: 600; }}
    .cluster-list {{ display: grid; gap: 18px; }}
    .cluster {{
      border-radius: var(--radius-lg);
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: var(--shadow);
      padding: 28px;
      backdrop-filter: blur(18px);
    }}
    .cluster-head {{ display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 18px; align-items: start; }}
    .cluster-title {{ margin: 0; font-size: 30px; letter-spacing: -0.04em; }}
    .cluster-desc {{ margin: 10px 0 0; color: var(--text2); line-height: 1.8; max-width: 840px; }}
    .cluster-stats {{ display: flex; gap: 10px; flex-wrap: wrap; justify-content: flex-end; }}
    .cluster-stat {{ background: rgba(17,17,17,0.05); padding: 12px 14px; border-radius: 18px; min-width: 108px; }}
    .cluster-stat-value {{ font-size: 24px; font-weight: 800; line-height: 1; }}
    .cluster-stat-label {{ margin-top: 6px; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; color: var(--text3); font-weight: 700; }}
    .cluster-topline {{ margin-top: 14px; color: var(--text3); font-size: 13px; line-height: 1.7; }}
    .cards {{ margin-top: 22px; display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }}
    .card {{ background: #fcfbf8; border-radius: 22px; border: 1px solid rgba(17,17,17,0.06); padding: 18px; display: grid; gap: 14px; }}
    .card-top {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; }}
    .date-chip {{ font-size: 12px; font-weight: 700; letter-spacing: 0.8px; text-transform: uppercase; color: var(--text3); }}
    .priority {{ display: inline-flex; align-items: center; padding: 7px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
    .priority-high {{ color: var(--high); background: rgba(214,69,69,0.1); }}
    .priority-mid {{ color: var(--mid); background: rgba(208,138,0,0.12); }}
    .priority-low {{ color: var(--low); background: rgba(95,125,66,0.12); }}
    .card h3 {{ margin: 0; font-size: 20px; line-height: 1.35; letter-spacing: -0.03em; }}
    .snippet {{ color: var(--text2); font-size: 14px; line-height: 1.8; }}
    .snippet strong {{ color: var(--text); display: block; margin-bottom: 4px; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }}
    .tags {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .tag {{ display: inline-flex; align-items: center; padding: 7px 10px; border-radius: 999px; background: rgba(17,17,17,0.05); color: var(--text2); font-size: 12px; font-weight: 700; }}
    .sources {{ display: grid; gap: 8px; }}
    .sources a {{ color: inherit; text-decoration: none; font-size: 13px; line-height: 1.5; padding: 10px 12px; border-radius: 14px; background: rgba(17,17,17,0.04); }}
    .sources a:hover {{ background: rgba(17,17,17,0.08); }}
    .footer-note {{ margin-top: 26px; color: var(--text3); font-size: 13px; line-height: 1.7; text-align: center; }}
    @media (max-width: 1120px) {{
      .hero {{ grid-template-columns: 1fr; }}
      .cluster-head {{ grid-template-columns: 1fr; }}
      .cluster-stats {{ justify-content: flex-start; }}
    }}
    @media (max-width: 720px) {{
      .wrap {{ padding: 22px 16px 48px; }}
      .hero, .cluster {{ padding: 22px; border-radius: 24px; }}
      .summary-grid {{ grid-template-columns: 1fr 1fr; }}
      .cards {{ grid-template-columns: 1fr; }}
      .toolbar-title {{ font-size: 20px; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div>
        <div class="eyebrow">Monthly demand map · standalone</div>
        <h1>{MONTH_LABEL} 需求整合 Map</h1>
        <p class="hero-desc">这版不再依赖页面临时扫日报，而是直接把 3 月所有需求挖掘先整合成一张静态地图。你现在看到的是一个月视角：哪条能力线最热、哪些机会优先级最高、每个方向具体有哪些需求卡片，都会直接铺开。</p>
        <div class="meta" id="meta"></div>
        <div class="highlights" id="highlights"></div>
      </div>
      <div class="summary-grid" id="summary"></div>
    </section>

    <div class="toolbar">
      <div class="toolbar-title">按功能聚类展开</div>
      <div class="legend" id="legend"></div>
    </div>

    <div class="cluster-list" id="cluster-list"></div>
    <div class="footer-note">生成时间：<span id="generated-at"></span> · 数据来源：{MONTH_KEY} 下所有日报 JSON 的 demand_mining 字段</div>
  </div>

  <script>
    const MAP_DATA = {data_json};

    function formatMonthDay(dateStr) {{
      const d = new Date(dateStr + 'T00:00:00');
      return `${{d.getMonth() + 1}}/${{d.getDate()}}`;
    }}

    function truncate(text, maxLength = 120) {{
      if (!text) return '';
      return text.length > maxLength ? `${{text.slice(0, maxLength).trim()}}…` : text;
    }}

    function priorityClass(priority) {{
      if (priority === '高') return 'priority-high';
      if (priority === '中') return 'priority-mid';
      return 'priority-low';
    }}

    function renderSummary() {{
      const summary = document.getElementById('summary');
      const cards = [
        ['本月需求总数', MAP_DATA.totalItems, `覆盖 ${{MAP_DATA.availableDays}} 个有数据日期`],
        ['高优机会数', MAP_DATA.highPriorityCount, '适合直接进入下月 shortlist'],
        ['功能聚类数', MAP_DATA.clusterCount, '先按功能面聚合，而不是按新闻话题拆散'],
        ['优先级结构', `${{MAP_DATA.priorityCounts['高']}} / ${{MAP_DATA.priorityCounts['中']}} / ${{MAP_DATA.priorityCounts['低']}}`, '高 / 中 / 低'],
      ];
      summary.innerHTML = cards.map(([label, value, note]) => `
        <div class="summary-card">
          <div class="summary-value">${{value}}</div>
          <div class="summary-label">${{label}}</div>
          <div class="summary-note">${{note}}</div>
        </div>
      `).join('');
    }}

    function renderMeta() {{
      document.getElementById('generated-at').textContent = MAP_DATA.generatedAt;
      document.getElementById('meta').innerHTML = [
        `${{MAP_DATA.monthLabel}} · ${{formatMonthDay(MAP_DATA.firstDate)}} – ${{formatMonthDay(MAP_DATA.lastDate)}}`,
        `共 ${{MAP_DATA.availableDays}} 天有日报数据`,
        `共 ${{MAP_DATA.clusters.length}} 个功能聚类`,
      ].map(text => `<span class="meta-chip">${{text}}</span>`).join('');
      document.getElementById('highlights').innerHTML = MAP_DATA.highlights.map(text => `<div class="highlight">${{text}}</div>`).join('');
      document.getElementById('legend').innerHTML = MAP_DATA.clusters.map(cluster => `<span class="legend-pill">${{cluster.label}} · ${{cluster.itemCount}}</span>`).join('');
    }}

    function renderSources(sourceUrls) {{
      if (!sourceUrls || sourceUrls.length === 0) return '';
      return `
        <div class="sources">
          ${{sourceUrls.map(source => `<a href="${{source.url}}" target="_blank" rel="noreferrer">${{source.name}}</a>`).join('')}}
        </div>
      `;
    }}

    function renderCluster(cluster) {{
      return `
        <section class="cluster">
          <div class="cluster-head">
            <div>
              <div class="eyebrow">Function cluster</div>
              <h2 class="cluster-title">${{cluster.label}}</h2>
              <p class="cluster-desc">${{cluster.description}}</p>
              <div class="cluster-topline">覆盖日期：${{formatMonthDay(cluster.earliestDate)}} – ${{formatMonthDay(cluster.latestDate)}} · 代表机会：${{cluster.topTitles.join(' / ')}}</div>
            </div>
            <div class="cluster-stats">
              <div class="cluster-stat">
                <div class="cluster-stat-value">${{cluster.itemCount}}</div>
                <div class="cluster-stat-label">累计机会</div>
              </div>
              <div class="cluster-stat">
                <div class="cluster-stat-value">${{cluster.activeDays}}</div>
                <div class="cluster-stat-label">覆盖日期</div>
              </div>
              <div class="cluster-stat">
                <div class="cluster-stat-value">${{cluster.highPriorityCount}}</div>
                <div class="cluster-stat-label">高优需求</div>
              </div>
            </div>
          </div>
          <div class="cards">
            ${{cluster.items.map(item => `
              <article class="card">
                <div class="card-top">
                  <span class="date-chip">${{formatMonthDay(item.date)}}</span>
                  <span class="priority ${{priorityClass(item.priority)}}">优先级：${{item.priority}}</span>
                </div>
                <h3>${{item.title}}</h3>
                <div class="snippet"><strong>机会点</strong>${{truncate(item.opportunity, 160)}}</div>
                <div class="snippet"><strong>为什么现在</strong>${{truncate(item.evidence, 120)}}</div>
                <div class="tags">${{item.tags.map(tag => `<span class="tag">${{tag}}</span>`).join('')}}</div>
                ${{renderSources(item.source_urls)}}
              </article>
            `).join('')}}
          </div>
        </section>
      `;
    }}

    function render() {{
      renderMeta();
      renderSummary();
      document.getElementById('cluster-list').innerHTML = MAP_DATA.clusters.map(renderCluster).join('');
    }}

    render();
  </script>
</body>
</html>
'''


def main() -> None:
    map_data = build_map_data()
    OUTPUT_FILE.write_text(build_html(map_data), encoding='utf-8')
    print(f'Wrote {OUTPUT_FILE}')
    print(f"{map_data['totalItems']} items / {map_data['clusterCount']} clusters / {map_data['availableDays']} days")


if __name__ == '__main__':
    main()
