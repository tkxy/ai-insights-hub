#!/usr/bin/env python3
"""
OpenVoice V2 TTS 本地代理服务
==============================
接收前端的文本请求，调用本地 OpenVoice V2 pipeline，
用用户声音克隆合成音频并返回 wav 流。

启动方式：
  python3 tts_proxy.py          # 默认端口 8124
  python3 tts_proxy.py --port 9000

注意：首次启动需要加载模型，大约需要 30-60 秒。
"""

import json
import os
import sys
import argparse
import time
import io
import tempfile
from http.server import HTTPServer, BaseHTTPRequestHandler

# ========== 环境准备 ==========
# 强制禁用 MPS
os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'

# 把 ffmpeg 加到 PATH
try:
    import imageio_ffmpeg
    ffmpeg_dir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
    os.environ['PATH'] = ffmpeg_dir + ':' + os.environ.get('PATH', '')
except ImportError:
    pass

import torch
import torch.backends.mps
torch.backends.mps.is_available = lambda: False

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.join(SCRIPT_DIR, 'openvoice_repo')
REF_AUDIO = os.path.join(SCRIPT_DIR, '1_ref.wav')
DEVICE = 'cpu'


# ========== 模型加载（启动时一次性完成）==========

class OpenVoiceTTS:
    """封装 OpenVoice V2 + MeloTTS pipeline，支持双声音"""

    def __init__(self):
        self.converter = None
        self.target_se = None  # 克隆声音（Alex）
        self.target_se_mia = None  # 第二声音（Mia）
        self.source_se = None
        self.source_se_mia = None  # Mia 的基础音色
        self.melo_model = None
        self.speaker_id_zh = None
        self.speaker_id_en = None  # 英文基础音色（用于Mia）
        self.speaker_id_mia = None  # Mia 专用音色
        self._loaded = False

    def load(self):
        if self._loaded:
            return

        os.chdir(REPO_DIR)
        t0 = time.time()

        print("[Model] 加载 ToneColorConverter...")
        from openvoice import se_extractor
        from openvoice.api import ToneColorConverter

        ckpt = os.path.join(REPO_DIR, 'checkpoints_v2', 'converter')
        self.converter = ToneColorConverter(
            f'{ckpt}/config.json', device=DEVICE
        )
        self.converter.load_ckpt(f'{ckpt}/checkpoint.pth')

        print("[Model] 提取参考音频声纹（Alex）...")
        self.target_se, _ = se_extractor.get_se(
            REF_AUDIO, self.converter, vad=True
        )

        print("[Model] 加载中文基础音色...")
        self.source_se = torch.load(
            os.path.join(REPO_DIR, 'checkpoints_v2', 'base_speakers', 'ses', 'zh.pth'),
            map_location=DEVICE
        )

        print("[Model] 加载英文基础音色（Mia用）...")
        self.source_se_mia = torch.load(
            os.path.join(REPO_DIR, 'checkpoints_v2', 'base_speakers', 'ses', 'en-us.pth'),
            map_location=DEVICE
        )

        # 提取第二参考音频（如果有）
        ref_mia = os.path.join(SCRIPT_DIR, '2_ref.wav')
        if os.path.exists(ref_mia):
            print("[Model] 提取参考音频声纹（Mia）...")
            self.target_se_mia, _ = se_extractor.get_se(
                ref_mia, self.converter, vad=True
            )
        else:
            print("[Model] 无 Mia 参考音频，使用英文基础音色...")
            self.target_se_mia = None

        print("[Model] 加载 MeloTTS...")
        from melo.api import TTS
        self.melo_model = TTS(language='ZH', device='cpu')

        speaker_ids = self.melo_model.hps.data.spk2id
        if hasattr(speaker_ids, 'ZH'):
            self.speaker_id_zh = speaker_ids.ZH
        else:
            self.speaker_id_zh = 0
        self.speaker_id_en = 0  # en-us 默认
        self.speaker_id_mia = 0

        self._loaded = True
        print(f"[Model] 全部加载完成，耗时 {time.time()-t0:.1f}s")

    def synthesize(self, text, speed=1.0, voice='alex'):
        """合成语音，返回 wav 二进制数据
        voice='alex': 使用克隆声音（你的声音）
        voice='mia': 使用第二音色（不同音色）
        """
        if not self._loaded:
            raise RuntimeError("模型未加载")

        os.chdir(REPO_DIR)

        # 文本过长截断
        if len(text) > 5000:
            text = text[:4900] + '……内容较长，已截断。'

        t0 = time.time()

        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_src:
            src_path = tmp_src.name

        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_out:
            out_path = tmp_out.name

        try:
            if voice == 'mia':
                # Mia: 英文音色 + 第二参考音色
                if self.target_se_mia:
                    # 有第二参考音频 → 英文音色转成Mia的声音
                    self.melo_model.tts_to_file(
                        text, self.speaker_id_en, src_path, speed=speed
                    )
                    self.converter.convert(
                        audio_src_path=src_path,
                        src_se=self.source_se_mia,
                        tgt_se=self.target_se_mia,
                        output_path=out_path
                    )
                else:
                    # 无第二参考 → 直接用英文音色，不转换
                    self.melo_model.tts_to_file(
                        text, self.speaker_id_en, src_path, speed=speed
                    )
                    import shutil
                    shutil.copy(src_path, out_path)
            else:
                # Alex: 中文音色 + 克隆声音
                self.melo_model.tts_to_file(
                    text, self.speaker_id_zh, src_path, speed=speed
                )
                self.converter.convert(
                    audio_src_path=src_path,
                    src_se=self.source_se,
                    tgt_se=self.target_se,
                    output_path=out_path
                )

            with open(out_path, 'rb') as f:
                audio_bytes = f.read()

            duration = time.time() - t0
            print(f"[TTS] 合成完成 [{voice}]: {len(audio_bytes)} bytes, "
                  f"文本 {len(text)} 字符, 耗时 {duration:.1f}s")
            return audio_bytes

        finally:
            for p in [src_path, out_path]:
                try:
                    os.unlink(p)
                except OSError:
                    pass


# 全局模型实例
tts_engine = OpenVoiceTTS()


# ========== HTTP 代理服务 ==========

class TTSProxyHandler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(200)
        self._set_cors_headers()
        self.end_headers()

    def do_POST(self):
        if self.path != '/tts':
            self.send_error(404, 'Not Found')
            return

        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            request_data = json.loads(body.decode('utf-8'))
        except (json.JSONDecodeError, ValueError) as e:
            self._send_json_error(400, f'请求体格式错误: {e}')
            return

        text = request_data.get('text', '').strip()
        if not text:
            self._send_json_error(400, '缺少 text 参数')
            return

        speed = request_data.get('speed', 1.0)
        voice = request_data.get('voice', 'alex')  # 'alex' 或 'mia'
        print(f"[TTS] 收到合成请求 [{voice}]: {len(text)} 字符")

        try:
            audio_bytes = tts_engine.synthesize(text, speed=speed, voice=voice)
        except Exception as e:
            print(f"[ERROR] 合成失败: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            self._send_json_error(502, f'合成失败: {e}')
            return

        self.send_response(200)
        self._set_cors_headers()
        self.send_header('Content-Type', 'audio/wav')
        self.send_header('Content-Length', str(len(audio_bytes)))
        self.end_headers()
        self.wfile.write(audio_bytes)

    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self._set_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ok",
                "provider": "openvoice-v2-local",
                "loaded": tts_engine._loaded
            }).encode('utf-8'))
            return
        self.send_error(404, 'Not Found')

    def _set_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _send_json_error(self, code, message):
        self.send_response(code)
        self._set_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode('utf-8'))

    def log_message(self, format, *args):
        print(f"[HTTP] {args[0]}")


def main():
    parser = argparse.ArgumentParser(description='OpenVoice V2 TTS 本地代理')
    parser.add_argument('--port', type=int, default=8124)
    args = parser.parse_args()

    print("🎙️  OpenVoice V2 TTS 本地代理")
    print("   正在加载模型（首次约 30-60 秒）...\n")

    tts_engine.load()

    server = HTTPServer(('127.0.0.1', args.port), TTSProxyHandler)
    print(f"\n🟢 代理已就绪")
    print(f"   端口: {args.port}")
    print(f"   合成接口: POST http://127.0.0.1:{args.port}/tts")
    print(f"   健康检查: GET  http://127.0.0.1:{args.port}/health")
    print(f"   按 Ctrl+C 停止\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] TTS 代理已停止")
        server.server_close()


if __name__ == '__main__':
    main()
