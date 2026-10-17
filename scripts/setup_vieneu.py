"""Install an isolated, reproducible VieNeu runtime and smoke-test the selected device."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', choices=['cuda', 'cpu'], default='cuda')
    args = parser.parse_args()
    target = ROOT / '.venv-vieneu'
    directory = ROOT / '.runtime' / 'vieneu'
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / 'tmp'
    temporary.mkdir(exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(temporary)
    __import__('tempfile').tempdir = str(temporary)
    env = dict(os.environ, HF_HOME=str(directory / 'models'), PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1')
    python = target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')

    def stage(message):
        print(message, flush=True)
        (directory / 'setup.json').write_text(json.dumps({'message': message}, ensure_ascii=False), encoding='utf-8')

    def run(*arguments):
        subprocess.run([str(python), *arguments], cwd=ROOT, env=env, check=True)

    stage('Đang tạo môi trường Python riêng…')
    needs_environment = not (target / 'Lib' / 'site-packages' / 'pip').exists() if os.name == 'nt' else not python.exists()
    if needs_environment:
        venv.EnvBuilder(with_pip=True).create(target)
    run('-m', 'pip', 'install', '--upgrade', 'pip')
    if args.device == 'cuda':
        stage('Đang cài PyTorch CUDA 12.8…')
        run('-m', 'pip', 'install', 'torch==2.8.0', 'torchaudio==2.8.0', '--index-url', 'https://download.pytorch.org/whl/cu128')
    stage('Đang cài VieNeu 3.8.3…')
    run('-m', 'pip', 'install', 'vieneu==3.8.3', 'transformers==4.57.6')
    run('-m', 'pip', 'check')
    stage('Đang tải model và thử tạo giọng; lần đầu có thể mất nhiều phút…')
    code = '''
import json, time, io, hashlib
from pathlib import Path
from vieneu import Vieneu
import soundfile as sf
device = DEVICE
if device == 'cuda':
    import torch
    assert torch.cuda.is_available(), 'CUDA chưa sẵn sàng; kiểm tra driver NVIDIA'
    torch.cuda.reset_peak_memory_stats()
started = time.monotonic()
tts = Vieneu(mode='v3turbo', device=device, backend='pytorch' if device == 'cuda' else 'onnx', max_batch_size=1, max_streams=1)
loaded = time.monotonic()
voices = tts.list_preset_voices()
assert voices, 'Không tìm thấy giọng có sẵn'
audio = tts.infer('Xin chào, đây là bản thử giọng tiếng Việt của Nambeo.', voice=voices[0][1])
assert len(audio) > 4800, 'Audio kết quả trống'
directory = Path('.runtime/vieneu')
sf.write(directory / 'setup-sample.wav', audio, 48000)
# Warm the cloning graphs too, so later offline clones need no extra download.
reference = audio[:8*48000]
sf.write(directory / 'setup-reference.wav', reference, 48000)
cloned = tts.infer('Đây là câu thử giọng cá nhân.', ref_audio=str(directory / 'setup-reference.wav'))
assert len(cloned) > 4800
catalog = [dict(id='vieneu:preset:'+identity, name=label, provider='vieneu', model='v3turbo', lang='vi-VN', gender='neutral', free=True, kind='preset') for label, identity in voices]
(directory / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
metrics = dict(device=device, load_seconds=round(loaded-started, 2), total_seconds=round(time.monotonic()-started, 2), audio_seconds=round(len(audio)/48000, 2))
refs = {str(p.relative_to(directory / 'models')):p.read_text().strip() for p in (directory / 'models').glob('hub/models--*/refs/*') if p.is_file()}
metrics['model_fingerprint'] = hashlib.sha256(json.dumps(refs, sort_keys=True).encode()).hexdigest()
if device == 'cuda': metrics['peak_vram_mb'] = round(torch.cuda.max_memory_allocated()/1024**2)
(directory / 'verified.json').write_text(json.dumps(metrics), encoding='utf-8')
print(json.dumps(metrics), flush=True)
'''.replace('DEVICE', repr(args.device))
    run('-c', code)
    stage('VieNeu đã cài và tạo giọng thử thành công.')


if __name__ == '__main__':
    main()
