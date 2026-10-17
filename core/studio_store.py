"""Versioned, atomic transcript storage shared by Studio and legacy writers.

The transcript's studio_config is authoritative. The .studio.json file is a
rebuildable mirror, so interruption between the two writes cannot split state.
"""
import copy
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path

LOCK = threading.RLock()
VOICE = {'provider': 'edge', 'model': 'edge', 'voice': 'vi-VN-HoaiMyNeural'}
STYLE = dict(font='DejaVu Sans', size=42, bold=True, color='#FFFFFF', outline_color='#000000',
             outline=2, shadow=1, background=False, background_color='#000000', opacity=0.6,
             x=0.5, y=0.9, width=0.86, safe_margin=0.05, align='center', line_spacing=1.15,
             bilingual=False, translated_first=True, secondary_size=32, secondary_color='#FFE08A',
             mask=False, mask_blur=True, mask_x=0.05, mask_y=0.8, mask_width=0.9, mask_height=0.15)
DEFAULT = dict(style=STYLE, voice=VOICE, speakers={}, clone_consent=False,
               mix={'voice_volume': 1.0, 'bg_volume': 0.15, 'ducking': True},
               render={'start': 0, 'end': None, 'speed': 1, 'aspect': 'original', 'dubbed': False, 'subtitles': True},
               reading_speed=20, timing={'max_speed': 1.65, 'max_overlap': 1.0}, caption_hold=12)

class Conflict(ValueError):
    pass

def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.studio-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def read_json(path, default):
    return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).exists() else copy.deepcopy(default)

def paths(video):
    base = os.path.splitext(str(video))[0]
    result = Path(base + '.transcript.json'), Path(base + '.studio.json'), Path(base + '.studio-assets')
    for path in result:
        safe_asset(video,path)
    return result

def safe_asset(video,path):
    root = os.path.realpath(os.path.dirname(str(video)))
    target = os.path.realpath(path)
    if os.path.commonpath([root,target]) != root:
        raise ValueError('Tài nguyên Studio dẫn ra ngoài thư mục video')
    return Path(path)

def source_text(seg):
    return seg.get('chinese') or seg.get('text') or ''

def dub_text(seg, language):
    if seg.get('no_dialogue'):
        return ''
    override = seg.get('dub_text')
    if override is not None:
        return override.strip()
    return (seg.get('vietnamese') or (source_text(seg) if language == 'vi' else '')).strip()

def normalize(data):
    if not isinstance(data, dict) or not isinstance(data.get('segments', []), list):
        raise ValueError('Bản thoại phải chứa danh sách câu')
    data = copy.deepcopy(data)
    data.setdefault('revision', 0)
    data['schema_version'] = 2
    for i, seg in enumerate(data.setdefault('segments', [])):
        if not isinstance(seg, dict):
            raise ValueError('Mỗi câu phải là một đối tượng')
        # Deterministic legacy IDs survive repeated read-only opens before save.
        seg.setdefault('id', str(uuid.uuid5(uuid.NAMESPACE_OID, f"{i}:{seg.get('start')}:{seg.get('end')}:{source_text(seg)}")))
        seg['id'] = str(seg['id'])
        seg.setdefault('speaker', seg.get('character', ''))
    config = copy.deepcopy(DEFAULT)
    if not isinstance(data.get('studio_config', {}), dict):
        raise ValueError('Cấu hình Studio không hợp lệ')
    for key, value in data.get('studio_config', {}).items():
        if key in config:
            if isinstance(config[key], dict):
                if not isinstance(value,dict):
                    raise ValueError(f'{key} phải là cấu hình')
                config[key].update(value if key=='speakers' else {k:v for k,v in value.items() if k in config[key]})
            else:
                config[key] = value
    data['studio_config'] = config
    return data

def load(video):
    transcript, _, _ = paths(video)
    return normalize(read_json(transcript, {'segments': [], 'detected_language': 'auto'}))

def number(value, low, high, label):
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label}: phải nằm trong {low}–{high}')
    return value

def validate(data, duration):
    if data.get('character_map') is not None and not isinstance(data['character_map'], dict):
        raise ValueError('Bảng nhân vật phải là một đối tượng')
    character_map = data.get('character_map') or {}
    if not isinstance(character_map.get('characters', []), list) or any(
        not isinstance(character, dict) or any(
            key in character and not isinstance(character[key], str)
            for key in ('name', 'role', 'self_pronoun', 'calls_others'))
        for character in character_map.get('characters', [])
    ):
        raise ValueError('Danh sách nhân vật không hợp lệ')
    seen = set()
    for seg in data['segments']:
        sid = str(seg['id'])
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', sid) or sid in seen:
            raise ValueError('ID câu không hợp lệ hoặc bị trùng')
        seen.add(sid)
        seg['start'] = number(seg['start'], 0, duration, 'Bắt đầu')
        seg['end'] = number(seg['end'], 0, duration, 'Kết thúc')
        if seg['start'] >= seg['end']:
            raise ValueError('Bắt đầu phải nhỏ hơn kết thúc')
        for key in ('text', 'chinese', 'vietnamese', 'speaker', 'dub_text'):
            if key in seg and seg[key] is not None and not isinstance(seg[key], str):
                raise ValueError(f'{key} phải là văn bản')
        if 'no_dialogue' in seg and not isinstance(seg['no_dialogue'], bool):
            raise ValueError('Không có thoại phải là bật/tắt')
    cfg = data['studio_config']
    style = cfg['style']
    if style['font'] != 'DejaVu Sans':
        raise ValueError('Font chưa được đóng gói')
    for key in ('color', 'outline_color', 'background_color', 'secondary_color'):
        if not re.fullmatch('#[0-9A-Fa-f]{6}', style[key]):
            raise ValueError('Màu không hợp lệ')
    for key in ('size', 'secondary_size'):
        style[key] = number(style[key], 12, 120, key)
    for key in ('x', 'y', 'opacity', 'mask_x', 'mask_y'):
        style[key] = number(style[key], 0, 1, key)
    for key in ('width', 'mask_width', 'mask_height'):
        style[key] = number(style[key], 0.05, 1, key)
    style['safe_margin'] = number(style['safe_margin'], 0, 0.25, 'Lề an toàn')
    style['outline'] = number(style['outline'], 0, 10, 'Viền')
    style['shadow'] = number(style['shadow'], 0, 10, 'Bóng')
    style['line_spacing'] = number(style['line_spacing'], 1, 2, 'Khoảng dòng')
    if style['align'] not in ('left', 'center', 'right'):
        raise ValueError('Căn chữ không hợp lệ')
    for key in ('mask_x', 'mask_y'):
        size_key = 'mask_width' if key.endswith('x') else 'mask_height'
        if style[key] + style[size_key] > 1.00001:
            raise ValueError('Vùng che phải nằm trong khung hình')
    for v in [cfg['voice'], *cfg['speakers'].values()]:
        if not isinstance(v,dict):
            raise ValueError('Cấu hình giọng không hợp lệ')
        if v.get('provider') not in ('edge', 'openai', 'omnivoice', 'vieneu'):
            raise ValueError('Provider TTS không hợp lệ')
        allowed_model = {'edge': 'edge', 'openai': 'tts-1', 'omnivoice': 'omnivoice', 'vieneu': 'v3turbo'}[v['provider']]
        if v.get('model') != allowed_model or not isinstance(v.get('voice'), str) or not v['voice']:
            raise ValueError('Model/giọng không hợp lệ')
        if set(v) - {'provider', 'model', 'voice'}:
            raise ValueError('Không lưu khóa hoặc tham số lạ trong cấu hình giọng')
        if v['provider'] == 'vieneu' and not re.fullmatch(r'vieneu:(preset:.{1,100}|custom:[0-9a-f]{32})', v['voice']):
            raise ValueError('ID giọng VieNeu không hợp lệ')
    for key in ('voice_volume', 'bg_volume'):
        cfg['mix'][key] = number(cfg['mix'][key], 0, 3, key)
    cfg['reading_speed'] = number(cfg['reading_speed'], 1, 100, 'Tốc độ đọc')
    cfg['timing']['max_speed'] = number(cfg['timing']['max_speed'], 1, 1.65, 'Tốc độ giọng tối đa')
    cfg['timing']['max_overlap'] = number(cfg['timing']['max_overlap'], 0, 1, 'Chồng tiếng tối đa')
    cfg['caption_hold'] = number(cfg['caption_hold'], 10, 15, 'Thời gian hiện phụ đề')
    render = cfg['render']
    render['start'] = number(render['start'], 0, duration, 'Trim bắt đầu')
    if render['end'] is not None:
        render['end'] = number(render['end'], 0, duration, 'Trim kết thúc')
    if (render['end'] if render['end'] is not None else duration) <= render['start']:
        raise ValueError('Khoảng trim không hợp lệ')
    render['speed'] = number(render['speed'], 0.5, 2, 'Tốc độ')
    if render['aspect'] not in ('original', '9:16', '16:9', '1:1'):
        raise ValueError('Tỷ lệ khung hình không hợp lệ')
    for key in ('bold','background','bilingual','translated_first','mask','mask_blur'):
        if not isinstance(style[key],bool):
            raise ValueError(f'{key} phải là bật/tắt')
    if not isinstance(cfg['clone_consent'],bool) or not isinstance(cfg['mix']['ducking'],bool) or not isinstance(render['dubbed'],bool) or not isinstance(render['subtitles'],bool):
        raise ValueError('Tùy chọn phải là bật/tắt')
    return data

def save(video, data, expected=None, duration=None):
    with LOCK:
        transcript, mirror, assets = paths(video)
        old = load(video)
        if expected is not None and old['revision'] != expected:
            raise Conflict('Bản thoại đã thay đổi. Tải lại trước khi lưu.')
        merged = copy.deepcopy(old)
        merged.update(copy.deepcopy(data))
        if not isinstance(merged.get('segments'),list) or any(not isinstance(seg,dict) for seg in merged['segments']):
            raise ValueError('Bản thoại phải chứa danh sách đối tượng câu')
        # Preserve IDs when old clients omit them.
        for i, seg in enumerate(merged.get('segments', [])):
            if 'id' not in seg and i < len(old['segments']):
                previous = old['segments'][i]
                if seg.get('start') == previous.get('start') and seg.get('end') == previous.get('end'):
                    seg['id'] = previous['id']
        merged = normalize(merged)
        if duration is not None:
            validate(merged, duration)
        versions = assets / 'versions'
        safe_asset(video,versions)
        versions.mkdir(parents=True, exist_ok=True)
        if transcript.exists():
            safe_asset(video,assets / 'original.transcript.json')
            if not (assets / 'original.transcript.json').exists():
                atomic_json(assets / 'original.transcript.json', read_json(transcript, {}))
            atomic_json(versions / f"{old['revision']:08d}.json", old)
        merged['revision'] = old['revision'] + 1
        # Legacy exports are derived views of the same canonical transcript.
        write_exports(video, merged)
        atomic_json(transcript, merged)
        # Mirror can always be rebuilt from the authoritative transcript.
        if merged.get('studio_enabled'):
            try:
                atomic_json(mirror, {'revision': merged['revision'], **merged['studio_config']})
            except OSError:
                pass
        for path in sorted(versions.glob('*.json'))[:-20]:
            safe_asset(video,path)
            path.unlink()
        return merged

def write_exports(video, data):
    from .transcriber import DouyinTranscriber
    base = os.path.splitext(str(video))[0]
    segments = [seg for seg in data['segments'] if not seg.get('no_dialogue')]
    views = {
        '.srt': [{**s, 'text': source_text(s)} for s in segments],
        '.vi.srt': [{**s, 'text': s.get('vietnamese', '')} for s in segments],
        '.bilingual.srt': [{**s, 'text': '\n'.join(filter(None, [s.get('vietnamese'), source_text(s)]))} for s in segments],
    }
    for suffix, cues in views.items():
        content = DouyinTranscriber.generate_srt(cues, 'text')
        atomic_text(Path(base + suffix), content)
    atomic_text(Path(base + '.transcript.txt'), DouyinTranscriber.generate_txt(data['segments'], include_timestamps=True))

def atomic_text(path, content):
    fd, tmp = tempfile.mkstemp(prefix='.studio-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def persist_legacy(video, data):
    return save(video, data, expected=data.get('revision'))

def voice_for(data, seg):
    cfg = data['studio_config']
    return cfg['speakers'].get(seg.get('speaker', ''), cfg['voice'])

def audio_key(data, seg, source_fingerprint=''):
    voice = voice_for(data, seg)
    payload = {'text': dub_text(seg, data.get('detected_language')), 'voice': voice}
    if voice['provider'] == 'vieneu':
        from .tts_runtime import library
        payload['engine_reference'] = library.fingerprint(voice['voice'])
    if voice['provider'] == 'omnivoice':
        payload['reference'] = [source_fingerprint, seg['start'], seg['end']]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def timing_key(data):
    cfg = data['studio_config']
    payload = {'version': 2, 'speed': cfg['render']['speed'], 'timing': cfg['timing'],
               'segments': [(s['id'], s['start'], s['end'], bool(dub_text(s, data.get('detected_language'))))
                            for s in sorted(data['segments'], key=lambda s: (s['start'], s['end'], s['id']))]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def warnings(data, media_info=None):
    from .studio_media import wrapped
    result = []
    ordered = sorted(data['segments'], key=lambda s: s['start'])
    for seg in ordered:
        if seg.get('no_dialogue'):
            continue
        text = seg.get('vietnamese') or source_text(seg)
        issues = []
        style = data['studio_config']['style']
        aspect=data['studio_config']['render']['aspect']
        ratio=(media_info['width']/media_info['height']) if media_info else 16/9
        if aspect!='original':
            a,b=map(float,aspect.split(':'));ratio=a/b
        available=ratio*1080*min(style['width'],1-2*style['safe_margin'])
        line_count=len(wrapped(text,available,style['size']))
        if style['bilingual'] and seg.get('vietnamese') and source_text(seg)!=seg['vietnamese']:
            line_count+=len(wrapped(source_text(seg),available,style['secondary_size']))
        if line_count > 2:
            issues.append('Vượt hai dòng')
        if issues:
            result.append({'id': seg['id'], 'messages': issues})
    return result
