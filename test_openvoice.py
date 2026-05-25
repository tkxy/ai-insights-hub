#!/usr/bin/env python3
"""快速测试 OpenVoice V2 完整 pipeline"""
import os, sys, torch, time

# 把 imageio-ffmpeg 的 ffmpeg 加到 PATH
try:
    import imageio_ffmpeg
    ffmpeg_dir = os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
    os.environ['PATH'] = ffmpeg_dir + ':' + os.environ.get('PATH', '')
except ImportError:
    pass

# 强制禁用 MPS，全部用 CPU
os.environ['PYTORCH_ENABLE_MPS_FALLBACK'] = '1'

os.chdir(os.path.join(os.path.dirname(__file__), 'openvoice_repo'))
t0 = time.time()

print('[1] Loading ToneColorConverter...')
from openvoice import se_extractor
from openvoice.api import ToneColorConverter

device = 'cpu'
ckpt = 'checkpoints_v2/converter'
converter = ToneColorConverter(f'{ckpt}/config.json', device=device)
converter.load_ckpt(f'{ckpt}/checkpoint.pth')
print(f'    Done in {time.time()-t0:.1f}s')

print('[2] Extracting speaker embedding from reference audio...')
t1 = time.time()
ref_audio = '../1_ref.wav'
target_se, audio_name = se_extractor.get_se(ref_audio, converter, vad=True)
print(f'    Done in {time.time()-t1:.1f}s, shape: {target_se.shape}')

print('[3] Loading MeloTTS (ZH)...')
t2 = time.time()
# Patch: 强制让 MeloTTS 不用 MPS
import torch.backends.mps
torch.backends.mps.is_available = lambda: False
from melo.api import TTS
model = TTS(language='ZH', device='cpu')
print(f'    Done in {time.time()-t2:.1f}s')

print('[4] Generating base speech...')
t3 = time.time()
os.makedirs('test_output', exist_ok=True)
src_path = 'test_output/tmp.wav'
speaker_ids = model.hps.data.spk2id
print(f'    Speaker IDs: {speaker_ids}')
for k in dir(speaker_ids):
    if not k.startswith('_'):
        print(f'      {k} = {getattr(speaker_ids, k, "?")}')
# 获取中文 speaker_id
if hasattr(speaker_ids, 'ZH'):
    speaker_id = speaker_ids.ZH
elif hasattr(speaker_ids, '__getitem__'):
    speaker_id = speaker_ids['ZH']
else:
    speaker_id = 0
print(f'    Using speaker_id: {speaker_id}')
model.tts_to_file('这是一段测试语音，用来验证声音克隆是否正常工作。', speaker_id, src_path, speed=1.0)
print(f'    Done in {time.time()-t3:.1f}s')

print('[5] Converting tone color...')
t4 = time.time()
source_se = torch.load('checkpoints_v2/base_speakers/ses/zh.pth', map_location=device)
out_path = 'test_output/cloned.wav'
converter.convert(
    audio_src_path=src_path,
    src_se=source_se,
    tgt_se=target_se,
    output_path=out_path
)
print(f'    Done in {time.time()-t4:.1f}s')

sz = os.path.getsize(out_path)
print(f'\n=== SUCCESS! Output: {out_path} ({sz} bytes) ===')
print(f'Total time: {time.time()-t0:.1f}s')
