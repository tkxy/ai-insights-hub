import json, sys

with open('data/2026-04-03.json', 'r') as f:
    data = json.load(f)

required_top = ['date', 'generated_at', 'daily_summary', 'design_insights', 'demand_mining', 'hot_news']
for field in required_top:
    assert field in data, f'Missing: {field}'

di_count = len(data['design_insights'])
dm_count = len(data['demand_mining'])
hn_count = len(data['hot_news'])
print(f'design_insights: {di_count} (3-6)')
print(f'demand_mining: {dm_count} (2-4)')
print(f'hot_news: {hn_count} (8-15)')

assert 3 <= di_count <= 6
assert 2 <= dm_count <= 4
assert 8 <= hn_count <= 15
assert dm_count < di_count

for i, di in enumerate(data['design_insights']):
    for f in ['title', 'summary', 'implication', 'sources', 'source_urls', 'tags']:
        assert f in di, f'di[{i}] missing {f}'
    assert len(di['tags']) == 4
    for su in di['source_urls']:
        assert 'name' in su and 'url' in su

for i, dm in enumerate(data['demand_mining']):
    for f in ['title', 'pain_point', 'opportunity', 'priority', 'evidence', 'source_urls']:
        assert f in dm, f'dm[{i}] missing {f}'
    assert dm['priority'] in ['高', '中', '低']

for i, hn in enumerate(data['hot_news']):
    expected = ['title', 'category', 'relevance', 'summary', 'source', 'source_url']
    keys = list(hn.keys())
    assert keys == expected, f'hn[{i}] order: {keys} != {expected}'
    assert hn['relevance'] in ['高', '中']
    assert isinstance(hn['source'], str)
    assert isinstance(hn['source_url'], str)

print(f'\n✅ All passed! {di_count}/{dm_count}/{hn_count}')
print(f'daily_summary: {len(data["daily_summary"])} chars')
