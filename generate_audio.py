#!/usr/bin/env python3
"""
播客音频预生成脚本
=================
读取当日 JSON 数据 → 按前端 generateDialogue 逻辑切分段 → 调本地 TTS → 输出音频文件

用法：
  python3 generate_audio.py                    # 生成今天的
  python3 generate_audio.py 2026-04-08         # 指定日期
  python3 generate_audio.py --tts-url http://127.0.0.1:8124/tts  # 指定 TTS 地址

输出：audio/{date}/ 目录下按段编号的 wav 文件 + manifest.json
"""

import json
import os
import re
import sys
import time
import argparse
import subprocess
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, 'data')
AUDIO_DIR = os.path.join(SCRIPT_DIR, 'audio')

DEFAULT_TTS_URL = 'http://127.0.0.1:8124/tts'

# 尝试获取 ffmpeg 路径
FFMPEG_BIN = None
try:
    import imageio_ffmpeg
    FFMPEG_BIN = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    # fallback: 系统 ffmpeg
    import shutil
    FFMPEG_BIN = shutil.which('ffmpeg')


def strip_html(text):
    """复刻前端 blogStripHtml"""
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('&nbsp;', ' ').replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    return text.strip()


def generate_dialogue(data):
    """复刻前端 generateDialogue，返回 [{role, text, section?}, ...]"""
    if not data:
        return []

    msgs = []
    date_display = data.get('date', '今天')

    # 开场白
    msgs.append({
        'role': 'host',
        'text': f'欢迎收听 {date_display} 的 AI Insights 播报，一起来看看今天有哪些不容错过的 AI 行业动态。'
    })

    # 今日速览
    if data.get('daily_summary'):
        msgs.append({
            'role': 'host',
            'text': f'先来一个全局速览。{data["daily_summary"]}',
            'section': '📋 今日速览'
        })

    # 深度洞察
    insights = data.get('design_insights', [])
    if insights:
        msgs.append({
            'role': 'host',
            'text': f'今天的深度洞察环节，一共有 {len(insights)} 个值得关注的话题。',
            'section': '🔍 深度洞察'
        })
        for idx, item in enumerate(insights):
            msgs.append({
                'role': 'host',
                'text': f'第 {idx + 1} 个话题：{item["title"]}。{item.get("summary", "")}'
            })
            extras = []
            if item.get('implication'):
                extras.append(f'我的理解是：{strip_html(item["implication"])}')
            if item.get('action'):
                extras.append(f'建议行动：{strip_html(item["action"])}')
            if extras:
                msgs.append({'role': 'host', 'text': ' '.join(extras)})

    # 需求挖掘
    demands = data.get('demand_mining', [])
    if demands:
        msgs.append({
            'role': 'host',
            'text': '接下来是需求挖掘环节，看看今天发现了哪些产品机会。',
            'section': '💎 需求挖掘'
        })
        for idx, item in enumerate(demands):
            parts = [f'第 {idx + 1} 个机会：{item["title"]}，优先级：{item.get("priority", "中")}。']
            if item.get('pain_point'):
                parts.append(f'用户痛点：{strip_html(item["pain_point"])}')
            if item.get('opportunity'):
                parts.append(f'产品机会：{strip_html(item["opportunity"])}')
            msgs.append({'role': 'host', 'text': ' '.join(parts)})

    # 快讯速递（每 2 条合一段）
    news = data.get('hot_news', [])
    if news:
        msgs.append({
            'role': 'host',
            'text': '最后来一组快讯。',
            'section': '⚡ 快讯速递'
        })
        batch = []
        for i, item in enumerate(news):
            t = item['title']
            if item.get('summary'):
                t += '。' + strip_html(item['summary'])
            batch.append(t)
            if len(batch) == 2 or i == len(news) - 1:
                msgs.append({'role': 'host', 'text': ' '.join(batch)})
                batch = []

    # 结束语
    msgs.append({
        'role': 'host',
        'text': '以上就是今天的全部内容，感谢收听，明天见。'
    })

    return msgs


def wav_to_mp3(wav_path, mp3_path):
    """用 ffmpeg 把 wav 转成 mp3 (128k)"""
    if not FFMPEG_BIN:
        return False
    try:
        subprocess.run([
            FFMPEG_BIN, '-y', '-i', wav_path,
            '-codec:a', 'libmp3lame', '-b:a', '128k',
            '-ar', '22050', mp3_path
        ], capture_output=True, timeout=30)
        if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 0:
            os.unlink(wav_path)
            return True
    except Exception as e:
        print(f'  [WARN] mp3 转换失败: {e}')
    return False


def synthesize_segment(tts_url, text, voice='alex', retries=2):
    """调用本地 TTS，返回 wav 二进制数据"""
    for attempt in range(retries + 1):
        try:
            resp = requests.post(tts_url, json={
                'text': text,
                'voice': voice,
                'source': 'ai-insights-hub-pregenerate'
            }, timeout=120)
            if resp.status_code == 200:
                return resp.content
            print(f'  [WARN] TTS 返回 {resp.status_code}, attempt {attempt + 1}')
        except requests.RequestException as e:
            print(f'  [WARN] TTS 请求失败: {e}, attempt {attempt + 1}')
        if attempt < retries:
            time.sleep(2)
    return None


def prune_old_audio(keep_days, current_date_str):
    """滚动清理旧音频目录。

    GitHub Pages 站点硬限制 1GB，音频约 6-12MB/天。
    不清理的话几个月就会撑爆部署。保留最近 keep_days 天即可 ——
    前端对没有预生成音频的日期会自动回落到浏览器 TTS，不会报错。
    """
    if keep_days <= 0:
        return []

    import re as _re
    from datetime import date as _date

    if not os.path.isdir(AUDIO_DIR):
        return []

    date_re = _re.compile(r'^\d{4}-\d{2}-\d{2}$')
    dirs = sorted(
        d for d in os.listdir(AUDIO_DIR)
        if date_re.match(d) and os.path.isdir(os.path.join(AUDIO_DIR, d))
    )
    # 保证当天一定保留
    keep = set(dirs[-keep_days:]) | {current_date_str}
    removed = []
    for d in dirs:
        if d in keep:
            continue
        path = os.path.join(AUDIO_DIR, d)
        size = sum(
            os.path.getsize(os.path.join(path, f))
            for f in os.listdir(path)
            if os.path.isfile(os.path.join(path, f))
        )
        import shutil as _shutil
        _shutil.rmtree(path)
        removed.append((d, size))
    return removed


def main():
    parser = argparse.ArgumentParser(description='播客音频预生成')
    parser.add_argument('date', nargs='?', default=None, help='日期 YYYY-MM-DD，默认今天')
    parser.add_argument('--provider', default='minimax',
                        choices=['minimax', 'local'],
                        help='TTS 提供方，默认 minimax（云端，无需本地服务）')
    parser.add_argument('--tts-url', default=DEFAULT_TTS_URL,
                        help='本地 TTS 地址（仅 --provider local 时使用）')
    parser.add_argument('--voice', default='alex', help='声音 ID（local 用）')
    parser.add_argument('--keep-days', type=int, default=7,
                        help='只保留最近 N 天音频，0 表示不清理。默认 7')
    args = parser.parse_args()

    # 确定日期
    if args.date:
        date_str = args.date
    else:
        from datetime import date
        date_str = date.today().isoformat()

    # 读取数据
    json_path = os.path.join(DATA_DIR, f'{date_str}.json')
    if not os.path.exists(json_path):
        print(f'[ERROR] 数据文件不存在: {json_path}')
        sys.exit(1)

    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 检查 TTS 可用性（失败即退出，不静默降级）
    mm = None
    if args.provider == 'minimax':
        try:
            import minimax_tts as mm
        except ImportError as e:
            print(f'[ERROR] 无法导入 minimax_tts: {e}')
            sys.exit(1)
        ok, msg = mm.health_check()
        if not ok:
            print(f'[ERROR] MiniMax 凭据检查失败: {msg}')
            sys.exit(1)
        print(f'[OK] MiniMax 就绪: {msg}')
    else:
        health_url = args.tts_url.replace('/tts', '/health')
        try:
            hc = requests.get(health_url, timeout=5)
            if not hc.ok:
                print(f'[ERROR] TTS 服务不健康: {hc.status_code}')
                sys.exit(1)
            print(f'[OK] 本地 TTS 就绪: {hc.json()}')
        except requests.RequestException as e:
            print(f'[ERROR] 本地 TTS 不可达: {e}')
            sys.exit(1)

    # 生成对话段
    dialogue = generate_dialogue(data)
    print(f'[INFO] 日期 {date_str}，共 {len(dialogue)} 段')

    # 创建输出目录
    out_dir = os.path.join(AUDIO_DIR, date_str)
    os.makedirs(out_dir, exist_ok=True)

    # 逐段合成
    manifest = {
        'date': date_str,
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'total_segments': len(dialogue),
        'segments': []
    }

    total_bytes = 0
    failed = 0
    t_start = time.time()

    for i, msg in enumerate(dialogue):
        text = strip_html(msg['text'])
        if not text:
            continue

        print(f'  [{i + 1}/{len(dialogue)}] {text[:50]}...' if len(text) > 50 else f'  [{i + 1}/{len(dialogue)}] {text}')

        if args.provider == 'minimax':
            # MiniMax 直接返回 mp3，不需要 ffmpeg 转码
            try:
                audio_data = mm.synthesize(text)
            except mm.MiniMaxError as e:
                print(f'    ❌ {e}')
                audio_data = None
        else:
            audio_data = synthesize_segment(args.tts_url, text,
                                            voice=args.voice)

        if audio_data:
            if args.provider == 'minimax':
                final_file = f'{i:03d}.mp3'
                with open(os.path.join(out_dir, final_file), 'wb') as f:
                    f.write(audio_data)
                final_size = len(audio_data)
            else:
                wav_filename = f'{i:03d}.wav'
                wav_path = os.path.join(out_dir, wav_filename)
                with open(wav_path, 'wb') as f:
                    f.write(audio_data)

                # 尝试转 mp3
                mp3_filename = f'{i:03d}.mp3'
                mp3_path = os.path.join(out_dir, mp3_filename)
                if wav_to_mp3(wav_path, mp3_path):
                    final_file = mp3_filename
                    final_size = os.path.getsize(mp3_path)
                else:
                    final_file = wav_filename
                    final_size = len(audio_data)

            total_bytes += final_size
            manifest['segments'].append({
                'index': i,
                'file': final_file,
                'text': text[:100],
                'section': msg.get('section', ''),
                'size': final_size
            })
            print(f'    ✅ {final_file} ({final_size} bytes)')
        else:
            failed += 1
            print(f'    ❌ 合成失败，跳过')

    # 写 manifest
    manifest['total_size_bytes'] = total_bytes
    manifest['failed'] = failed
    manifest['duration_seconds'] = round(time.time() - t_start, 1)

    manifest_path = os.path.join(out_dir, 'manifest.json')
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f'\n[DONE] {date_str}')
    print(f'  段数: {len(manifest["segments"])}/{len(dialogue)} (失败 {failed})')
    print(f'  总大小: {total_bytes / 1024:.0f} KB')
    print(f'  耗时: {manifest["duration_seconds"]}s')
    print(f'  输出: {out_dir}/')

    # 滚动清理，避免仓库无限膨胀
    removed = prune_old_audio(args.keep_days, date_str)
    if removed:
        freed = sum(s for _, s in removed) / 1024 / 1024
        print(f'\n[PRUNE] 清理 {len(removed)} 天旧音频，释放 {freed:.0f} MB')
        for d, s in removed:
            print(f'  - {d} ({s / 1024 / 1024:.1f} MB)')

    if failed > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
