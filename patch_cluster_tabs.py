#!/usr/bin/env python3
"""Patch index.html: make cluster chips clickable tabs that toggle cluster sections."""
import re
from pathlib import Path

f = Path(__file__).resolve().parent / 'index.html'
html = f.read_text('utf-8')

# 1. Replace cluster chip row: add onclick + data-cluster + active style for first one
clusters = ['帮写类', '打字/输入类', '服务/Agent类', '安全/信任类', '视觉/多模态类', '语音类']
counts = {'帮写类': 20, '打字/输入类': 13, '服务/Agent类': 7, '安全/信任类': 6, '视觉/多模态类': 5, '语音类': 3}

chip_style_base = 'padding:8px 14px;border-radius:999px;font-size:13px;font-weight:600;cursor:pointer;border:none;font-family:inherit;transition:all 0.2s;'
chip_active = chip_style_base + 'background:#111;color:#fff;'
chip_normal = chip_style_base + 'background:rgba(0,0,0,0.05);color:#555;'

new_chips = '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:28px;">'
for i, name in enumerate(clusters):
    style = chip_active if i == 0 else chip_normal
    new_chips += f'<button class="cluster-chip{" active" if i==0 else ""}" data-cluster="{name}" onclick="switchCluster(\'{name}\')" style="{style}">{name} · {counts[name]}</button>'
new_chips += '</div>'

# Find and replace old chip row
old_chip_pattern = r'<div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:28px;">.*?</div>'
html = re.sub(old_chip_pattern, new_chips, html, count=1)

# 2. Add data-cluster attribute to each cluster section
# Each cluster section starts with: <div style="background:rgba(255,255,255,0.88);border-radius:24px;...
# and contains <h3 ...>帮写类</h3> etc.
for name in clusters:
    # Find the h3 with this cluster name and add data-cluster to its parent section div
    old = f'''<div style="background:rgba(255,255,255,0.88);border-radius:24px;border:1px solid rgba(0,0,0,0.06);padding:28px;margin-bottom:18px;">
          <div style="display:flex;justify-content:space-between;gap:18px;align-items:start;margin-bottom:20px;flex-wrap:wrap;">
            <div>
              <div style="font-size:11px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;color:#888;margin-bottom:10px;">Function cluster</div>
              <h3 style="font-size:28px;font-weight:900;letter-spacing:-1px;margin-bottom:8px;">{name}</h3>'''
    
    display = 'block' if name == clusters[0] else 'none'
    new = f'''<div class="cluster-block" data-cluster="{name}" style="display:{display};background:rgba(255,255,255,0.88);border-radius:24px;border:1px solid rgba(0,0,0,0.06);padding:28px;margin-bottom:18px;">
          <div style="display:flex;justify-content:space-between;gap:18px;align-items:start;margin-bottom:20px;flex-wrap:wrap;">
            <div>
              <div style="font-size:11px;font-weight:700;letter-spacing:1.6px;text-transform:uppercase;color:#888;margin-bottom:10px;">Function cluster</div>
              <h3 style="font-size:28px;font-weight:900;letter-spacing:-1px;margin-bottom:8px;">{name}</h3>'''
    
    html = html.replace(old, new, 1)

# 3. Add switchCluster JS before </script>
switch_js = """
function switchCluster(name) {
  var blocks = document.querySelectorAll('.cluster-block');
  for (var i = 0; i < blocks.length; i++) {
    blocks[i].style.display = blocks[i].getAttribute('data-cluster') === name ? 'block' : 'none';
  }
  var chips = document.querySelectorAll('.cluster-chip');
  for (var i = 0; i < chips.length; i++) {
    if (chips[i].getAttribute('data-cluster') === name) {
      chips[i].classList.add('active');
      chips[i].style.background = '#111';
      chips[i].style.color = '#fff';
    } else {
      chips[i].classList.remove('active');
      chips[i].style.background = 'rgba(0,0,0,0.05)';
      chips[i].style.color = '#555';
    }
  }
}
"""
html = html.replace('</script>', switch_js + '</script>', 1)

f.write_text(html, 'utf-8')
print('Patched! cluster-chip count:', html.count('cluster-chip'))
print('cluster-block count:', html.count('cluster-block'))
