#!/usr/bin/env python3
"""
MiniMax TTS 合成模块
====================
替代原来的 OpenVoice 本地方案（tts_proxy.py）。
不需要本地服务、不需要加载模型，直接调云端 API。

凭据从环境变量读取：
  MINIMAX_API_KEY    必填，开放平台 API Key
  MINIMAX_GROUP_ID   选填，部分接口需要
  MINIMAX_VOICE_ID   必填，克隆音色 ID（如 alex 的音色）
  MINIMAX_MODEL      选填，默认 speech-2.8-hd

单独测试：
  python3 minimax_tts.py "测试一下这个声音"
  python3 minimax_tts.py --list-voices
"""

import os
import sys
import time
import json
import argparse

import requests

# 国内站；国际站为 https://api.minimax.io/v1
API_BASE = os.environ.get('MINIMAX_API_BASE', 'https://api.minimaxi.com/v1')

DEFAULT_MODEL = os.environ.get('MINIMAX_MODEL', 'speech-2.8-hd')

# 播客场景的音频参数。
# 语音播报不需要 128kbps —— 32kHz/64kbps 单声道人声已经很干净，
# 相比默认 128kbps 体积直接砍一半，这对 GitHub Pages 1GB 限制很关键。
AUDIO_SETTING = {
    'sample_rate': 32000,
    'bitrate': 64000,
    'format': 'mp3',
    'channel': 1,
}


class MiniMaxError(RuntimeError):
    pass


def _get_credentials():
    api_key = os.environ.get('MINIMAX_API_KEY', '').strip()
    voice_id = os.environ.get('MINIMAX_VOICE_ID', '').strip()
    if not api_key:
        raise MiniMaxError(
            '缺少 MINIMAX_API_KEY 环境变量。\n'
            '  export MINIMAX_API_KEY="你的key"'
        )
    if not voice_id:
        raise MiniMaxError(
            '缺少 MINIMAX_VOICE_ID 环境变量。\n'
            '  export MINIMAX_VOICE_ID="你的克隆音色id"'
        )
    return api_key, voice_id


def synthesize(text, speed=1.0, voice_id=None, model=None,
               emotion=None, retries=2, timeout=120):
    """合成语音，返回 mp3 二进制数据。

    失败时抛 MiniMaxError —— 不静默返回 None，
    避免上游把失败当成功继续跑（这个坑踩过）。
    """
    api_key, default_voice = _get_credentials()
    voice_id = voice_id or default_voice
    model = model or DEFAULT_MODEL

    voice_setting = {
        'voice_id': voice_id,
        'speed': speed,
        'vol': 1,
        'pitch': 0,
    }
    if emotion:
        voice_setting['emotion'] = emotion

    payload = {
        'model': model,
        'text': text,
        'stream': False,
        'voice_setting': voice_setting,
        'audio_setting': AUDIO_SETTING,
        'output_format': 'hex',
    }

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }

    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                f'{API_BASE}/t2a_v2',
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            if resp.status_code != 200:
                last_err = f'HTTP {resp.status_code}: {resp.text[:200]}'
            else:
                body = resp.json()
                base = body.get('base_resp', {})
                code = base.get('status_code', -1)
                if code != 0:
                    last_err = f'API 错误 {code}: {base.get("status_msg", "")}'
                else:
                    audio_hex = (body.get('data') or {}).get('audio', '')
                    if not audio_hex:
                        last_err = 'API 返回空音频'
                    else:
                        return bytes.fromhex(audio_hex)
        except requests.RequestException as e:
            last_err = f'请求异常: {e}'
        except (ValueError, json.JSONDecodeError) as e:
            last_err = f'响应解析失败: {e}'

        if attempt < retries:
            wait = 2 * (attempt + 1)
            print(f'  [WARN] {last_err}，{wait}s 后重试 '
                  f'({attempt + 1}/{retries})', file=sys.stderr)
            time.sleep(wait)

    raise MiniMaxError(f'合成失败（已重试 {retries} 次）: {last_err}')


def health_check():
    """用一段极短文本验证凭据可用，返回 (ok, message)。"""
    try:
        data = synthesize('测试', retries=0, timeout=30)
        return True, f'凭据可用，返回 {len(data)} bytes'
    except MiniMaxError as e:
        return False, str(e)


def list_voices(voice_type='all'):
    """列出账号下所有音色 ID。

    ⚠️ 官方限制：克隆音色（voice_cloning）创建后处于 inactive 状态，
    必须先成功用于一次语音合成，才能被本接口查询到。
    所以刚克隆完就来查，很可能是空列表 —— 这不是 bug。
    这种情况直接去控制台网页看，或先拿 voice_id 合成一次。
    """
    api_key = os.environ.get('MINIMAX_API_KEY', '').strip()
    if not api_key:
        raise MiniMaxError(
            '缺少 MINIMAX_API_KEY 环境变量。\n'
            '  export MINIMAX_API_KEY="你的key"'
        )

    resp = requests.post(
        f'{API_BASE}/get_voice',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        json={'voice_type': voice_type},
        timeout=60,
    )
    if resp.status_code != 200:
        raise MiniMaxError(f'HTTP {resp.status_code}: {resp.text[:300]}')

    body = resp.json()
    base = body.get('base_resp', {})
    if base.get('status_code', -1) != 0:
        raise MiniMaxError(
            f'API 错误 {base.get("status_code")}: {base.get("status_msg")}')
    return body


def _print_voices(body):
    cloning = body.get('voice_cloning') or []
    generation = body.get('voice_generation') or []
    system = body.get('system_voice') or []

    print(f'\n🎙️  克隆音色 voice_cloning（{len(cloning)}）')
    if cloning:
        for v in cloning:
            print(f'  ★ {v.get("voice_id")}'
                  f'   创建于 {v.get("created_time", "?")}')
    else:
        print('  （空）刚克隆的音色需先成功合成一次才会出现在这里，')
        print('       去控制台网页看更可靠：https://platform.minimaxi.com/')

    if generation:
        print(f'\n🔊 文生音色 voice_generation（{len(generation)}）')
        for v in generation:
            print(f'  - {v.get("voice_id")}   {v.get("created_time", "")}')

    print(f'\n📚 系统预设音色 system_voice（{len(system)}）')
    for v in system[:8]:
        name = v.get('voice_name', '')
        print(f'  - {v.get("voice_id")}' + (f'   {name}' if name else ''))
    if len(system) > 8:
        print(f'  ... 另有 {len(system) - 8} 个，加 --all-system 查看全部')


def clone_voice(ref_audio_path, voice_id, model='speech-2.8-hd',
                preview_text=None):
    """上传参考音频并克隆音色。返回 voice_id。

    参考音频要求：清晰人声，10s-5min（建议 15s），wav/mp3/m4a。
    voice_id 规则：长度 8-256，字母开头，只含字母数字 - _，不能以 - _ 结尾。
    """
    api_key, _ = os.environ.get('MINIMAX_API_KEY', '').strip(), None
    if not api_key:
        raise MiniMaxError('缺少 MINIMAX_API_KEY')

    if not os.path.exists(ref_audio_path):
        raise MiniMaxError(f'参考音频不存在: {ref_audio_path}')

    # Step 1: 上传
    print(f'[1/2] 上传参考音频 {ref_audio_path} ...')
    with open(ref_audio_path, 'rb') as f:
        up = requests.post(
            f'{API_BASE}/files/upload',
            headers={'Authorization': f'Bearer {api_key}'},
            files={'file': (os.path.basename(ref_audio_path), f)},
            data={'purpose': 'voice_clone'},
            timeout=120,
        )
    if up.status_code != 200:
        raise MiniMaxError(f'上传失败 HTTP {up.status_code}: {up.text[:300]}')
    up_body = up.json()
    file_id = (up_body.get('file') or {}).get('file_id')
    if not file_id:
        raise MiniMaxError(f'上传响应无 file_id: {up_body}')
    print(f'      file_id = {file_id}')

    # Step 2: 克隆
    print(f'[2/2] 克隆音色 voice_id={voice_id} ...')
    clone_payload = {'file_id': file_id, 'voice_id': voice_id, 'model': model}
    if preview_text:
        clone_payload['text'] = preview_text
    cl = requests.post(
        f'{API_BASE}/voice_clone',
        headers={
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        },
        json=clone_payload,
        timeout=180,
    )
    if cl.status_code != 200:
        raise MiniMaxError(f'克隆失败 HTTP {cl.status_code}: {cl.text[:300]}')
    cl_body = cl.json()
    base = cl_body.get('base_resp', {})
    if base.get('status_code', -1) != 0:
        raise MiniMaxError(
            f'克隆失败 {base.get("status_code")}: {base.get("status_msg")}')

    demo = cl_body.get('demo_audio', '')
    if demo:
        print(f'      试听: {demo}')
    return voice_id


def main():
    parser = argparse.ArgumentParser(description='MiniMax TTS 测试工具')
    parser.add_argument('text', nargs='?', help='要合成的文本')
    parser.add_argument('-o', '--output', default='minimax_test.mp3')
    parser.add_argument('--speed', type=float, default=1.0)
    parser.add_argument('--voice-id', default=None)
    parser.add_argument('--model', default=None)
    parser.add_argument('--health', action='store_true', help='只做凭据检查')
    parser.add_argument('--list-voices', action='store_true',
                        help='列出账号下所有音色 ID（含已克隆的）')
    parser.add_argument('--all-system', action='store_true',
                        help='配合 --list-voices，完整列出系统音色')
    parser.add_argument('--clone', metavar='REF_AUDIO',
                        help='克隆音色，传参考音频路径')
    parser.add_argument('--clone-id', help='克隆时指定的新 voice_id')
    args = parser.parse_args()

    if args.list_voices:
        try:
            body = list_voices()
        except MiniMaxError as e:
            print(f'❌ {e}', file=sys.stderr)
            sys.exit(1)
        if args.all_system:
            for v in (body.get('system_voice') or []):
                print(f'{v.get("voice_id")}\t{v.get("voice_name", "")}')
        else:
            _print_voices(body)
        return

    if args.health:
        ok, msg = health_check()
        print(('✅ ' if ok else '❌ ') + msg)
        sys.exit(0 if ok else 1)

    if args.clone:
        if not args.clone_id:
            print('❌ --clone 需要同时指定 --clone-id', file=sys.stderr)
            sys.exit(1)
        try:
            vid = clone_voice(args.clone, args.clone_id)
            print(f'\n✅ 克隆完成，voice_id = {vid}')
            print(f'   下一步: export MINIMAX_VOICE_ID="{vid}"')
        except MiniMaxError as e:
            print(f'❌ {e}', file=sys.stderr)
            sys.exit(1)
        return

    if not args.text:
        parser.print_help()
        sys.exit(1)

    try:
        t0 = time.time()
        data = synthesize(args.text, speed=args.speed,
                          voice_id=args.voice_id, model=args.model)
    except MiniMaxError as e:
        print(f'❌ {e}', file=sys.stderr)
        sys.exit(1)

    with open(args.output, 'wb') as f:
        f.write(data)
    print(f'✅ {args.output} ({len(data)} bytes, '
          f'{time.time() - t0:.1f}s, {len(args.text)} 字符)')


if __name__ == '__main__':
    main()
