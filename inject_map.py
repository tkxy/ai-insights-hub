#!/usr/bin/env python3
"""Inject pre-rendered Map page into index.html. Insight page stays untouched."""

from pathlib import Path

BASE = Path(__file__).resolve().parent

html = (BASE / 'index.html').read_text('utf-8')
map_html = Path('/tmp/map-fragment.html').read_text('utf-8')

# 1. Insert #map-page between #app's closing </div> and <div class="footer">
map_block = (
    '\n<div class="main" id="map-page" style="display:none; max-width:1400px; margin:0 auto; padding:20px 48px 60px;">\n'
    + map_html
    + '\n</div>\n'
)

old_marker = '</div>\n\n<div class="footer">'
new_marker = '</div>\n' + map_block + '\n<div class="footer">'
html = html.replace(old_marker, new_marker, 1)

# 2. Add switchPage JS before </script>
switch_js = """
function switchPage(id) {
  var app = document.getElementById('app');
  var mapPage = document.getElementById('map-page');
  var btns = document.querySelectorAll('.nav-link');
  if (id === 'map') {
    app.style.display = 'none';
    mapPage.style.display = 'block';
  } else {
    app.style.display = 'block';
    mapPage.style.display = 'none';
  }
  for (var i = 0; i < btns.length; i++) {
    btns[i].classList.remove('active');
    if ((id === 'map' && btns[i].textContent === 'Map') ||
        (id === 'insight' && btns[i].textContent === 'Insight')) {
      btns[i].classList.add('active');
    }
  }
  window.scrollTo(0, 0);
}
if (window.location.hash === '#map') {
  document.addEventListener('DOMContentLoaded', function() { switchPage('map'); });
}
"""
html = html.replace('</script>', switch_js + '</script>')

(BASE / 'index.html').write_text(html, 'utf-8')
print(f'Done! index.html is now {len(html)} bytes')
