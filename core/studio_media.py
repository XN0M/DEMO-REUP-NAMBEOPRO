"""Shared ASS renderer, cancellable FFmpeg and per-cue audio alignment."""
import math
import os
import re
import subprocess
import tempfile
import textwrap
import time
import wave
from pathlib import Path
import cv2
import imageio_ffmpeg
import numpy as np
from .studio_store import source_text
from .dub_timing import captions

FONT_DIR = Path(__file__).resolve().parents[1] / 'web' / 'fonts'
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

class Cancelled(Exception):
    pass

def run(args, cancelled=lambda: False, cwd=None):
    # Redirect stderr to disk to avoid pipe deadlocks on long encodes.
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen([FFMPEG, '-nostdin', '-y', '-v', 'error', *map(str, args)],
                                   cwd=cwd, stdout=subprocess.DEVNULL, stderr=log)
        try:
            while process.poll() is None:
                if cancelled():
                    raise Cancelled('Đã hủy')
                time.sleep(.1)
            if cancelled():
                raise Cancelled('Đã hủy')
            if process.returncode:
                log.seek(0)
                raise RuntimeError(log.read().decode('utf-8', 'replace')[-4000:])
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()

def probe(video):
    cap = cv2.VideoCapture(str(video))
    try:
        width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps, frames = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT)
    finally:
        cap.release()
    if width <= 0 or height <= 0 or fps <= 0:
        raise ValueError('Không đọc được video')
    return {'width': width, 'height': height, 'duration': frames / fps, 'fps': fps}

def has_audio(path):
    process = subprocess.run([FFMPEG, '-i', str(path)], capture_output=True)
    return b'Audio:' in process.stderr

def color(hex_value, opacity=1):
    return f'&H{round((1-opacity)*255):02X}{hex_value[5:7]}{hex_value[3:5]}{hex_value[1:3]}'

def stamp(seconds):
    cs = max(0, round(seconds * 100))
    return f'{cs//360000}:{cs//6000%60:02d}:{cs//100%60:02d}.{cs%100:02d}'

def plain(text):
    # Prevent ASS override injection, including literal backslash escape sequences.
    return str(text).replace('\\', '／').replace('{', '｛').replace('}', '｝').replace('\r', '')

def wrapped(text, width, size):
    columns = max(5, int(width / (size * .57)))
    return [line for paragraph in plain(text).split('\n') for line in (textwrap.wrap(paragraph, columns, break_long_words=True) or [''])]

def ass_document(data, width, height, start, end, speed, entries=None):
    style = data['studio_config']['style']
    # Font sizes use a reference height of 1080; scale identically in both renders.
    size, secondary = style['size'] * height / 1080, style['secondary_size'] * height / 1080
    margin = style['safe_margin']
    x = min(1-margin, max(margin, style['x'])) * width
    y = min(1-margin, max(margin, style['y'])) * height
    align = {'left': 1, 'center': 2, 'right': 3}[style['align']]
    if align == 1:
        x = max(margin * width, x - style['width'] * width / 2)
    if align == 3:
        x = min((1-margin) * width, x + style['width'] * width / 2)
    background = color(style['background_color'], style['opacity'])
    header = f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,{size},{color(style['color'])},{color(style['color'])},{color(style['outline_color'])},{background},{-1 if style['bold'] else 0},0,0,0,100,100,0,0,1,{style['outline']*height/1080},{style['shadow']*height/1080},{align},0,0,0,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    events = []
    windows = captions(data['segments'], data['studio_config'], entries)
    for seg in data['segments']:
        cue_start, cue_end = windows[seg['id']]
        a, b = max(start, cue_start), min(end, cue_end)
        if b <= a:
            continue
        vi, original = seg.get('vietnamese', ''), source_text(seg)
        groups = [(vi or original, size, style['color'])]
        if style['bilingual'] and vi and original and vi != original:
            groups = [(vi, size, style['color']), (original, secondary, style['secondary_color'])]
            if not style['translated_first']:
                groups.reverse()
        lines = [(line, font_size, col) for text, font_size, col in groups
                 for line in wrapped(text, width * min(style['width'], 1 - 2*margin), font_size)]
        total_height = sum(font_size * style['line_spacing'] for _, font_size, _ in lines)
        bottom = min(height * (1-margin), max(height * margin + total_height, y))
        cursor = bottom - total_height
        for line, font_size, col in lines:
            cursor += font_size * style['line_spacing']
            prefix = f'{{\\an{align}\\pos({x:.2f},{cursor:.2f})\\fs{font_size:.2f}\\1c{color(col)}&}}'
            if style['background']:
                box = f'{{\\an{align}\\pos({x:.2f},{cursor:.2f})\\fs{font_size:.2f}\\bord3\\shad0\\3c{background}&\\3a&H{round((1-style["opacity"])*255):02X}&\\1a&HFF&}}'
                # Separate opaque-box style event preserves the independent text outline.
                events.append(f'Dialogue: 0,{stamp((a-start)/speed)},{stamp((b-start)/speed)},Box,,0,0,0,,{box}{line}')
            events.append(f'Dialogue: 1,{stamp((a-start)/speed)},{stamp((b-start)/speed)},Default,,0,0,0,,{prefix}{line}')
    box_style = f'Style: Box,DejaVu Sans,{size},&HFF000000,&HFF000000,{background},{background},0,0,0,0,100,100,0,0,3,3,0,{align},0,0,0,1\n'
    header = header.replace('[Events]', box_style + '[Events]')
    return header + '\n'.join(events) + '\n'

def atempo(speed):
    filters = []
    while speed > 2:
        filters.append('atempo=2'); speed /= 2
    while speed < .5:
        filters.append('atempo=0.5'); speed *= 2
    return ','.join(filters + [f'atempo={speed:.8f}'])

def align_audio(raw, output, speed, cancelled):
    with wave.open(str(raw), 'rb') as audio:
        duration = audio.getnframes() / audio.getframerate()
    if duration <= 0:
        raise ValueError('Audio trống')
    run(['-i', raw, '-af', atempo(speed), '-ac', '1', '-ar', '24000', '-c:a', 'pcm_s16le', output], cancelled)
    with wave.open(str(output), 'rb') as aligned:
        fitted = aligned.getnframes() / aligned.getframerate()
    return {'status': 'ready', 'duration': duration, 'aligned_duration': fitted, 'tempo_factor': speed}

def make_voice_track(data, entries, assets, output, duration, cancelled):
    # Accumulate in a disk-backed array: adjacent cues may overlap without overwriting each other.
    pcm = Path(output).with_suffix('.pcm')
    counts_path = Path(output).with_suffix('.counts')
    ordered = sorted(data['segments'], key=lambda seg: seg['start'])
    frames_total = max(1, math.ceil(max([duration] + [max(entries[s['id']].get('play_end', 0),
        entries[s['id']].get('play_start', s['start']) + entries[s['id']].get('aligned_duration', 0))
        for s in ordered if s['id'] in entries and entries[s['id']]['status'] == 'ready']) * 24000) + 2400)
    accumulator = np.memmap(pcm, dtype='<i4', mode='w+', shape=(frames_total,))
    counts = np.memmap(counts_path, dtype='u1', mode='w+', shape=(frames_total,))
    accumulator[:] = 0
    counts[:] = 0
    try:
        for seg in ordered:
            entry = entries.get(seg['id'])
            if not entry or entry['status'] != 'ready':
                continue
            if cancelled():
                raise Cancelled()
            with wave.open(str(assets / entry['aligned']), 'rb') as audio:
                frames = audio.readframes(audio.getnframes())
            samples = np.frombuffer(frames, dtype='<i2').astype(np.int32)
            offset = round(entry.get('play_start', seg['start']) * 24000)
            last = min(frames_total, offset + len(samples))
            accumulator[offset:last] += samples[:last-offset]
            counts[offset:last] += 1
        with wave.open(str(output), 'wb') as stream:
            stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(24000)
            for offset in range(0, frames_total, 240000):
                if cancelled():
                    raise Cancelled()
                end = min(frames_total, offset + 240000)
                chunk = accumulator[offset:end].astype(np.float32)
                np.divide(chunk, np.maximum(counts[offset:end], 1), out=chunk)
                stream.writeframes(np.clip(chunk, -32768, 32767).astype('<i2').tobytes())
    finally:
        del accumulator, counts
        pcm.unlink(missing_ok=True)
        counts_path.unlink(missing_ok=True)

def render(video, data, output, work, cancelled, voice_track=None, preview_at=None, entries=None):
    info = probe(video)
    cfg, style = data['studio_config'], data['studio_config']['style']
    options = cfg['render']
    start, end, speed = options['start'], options['end'] or info['duration'], options['speed']
    if preview_at is not None:
        start = max(start, min(end - .1, preview_at - 2.5 * speed))
        end = min(end, start + 5 * speed)
    eligible = {seg['id'] for seg in data['segments'] if seg['start'] < end and seg['end'] > start}
    tail = max(0.0, max((max(entry.get('play_end', 0), entry.get('play_start', 0) + entry.get('aligned_duration', 0))
                         for sid, entry in (entries or {}).items() if sid in eligible and entry.get('status') == 'ready'
                         and entry.get('play_end', 0) > start), default=end) - end) if voice_track else 0.0
    width, height = info['width'], info['height']
    aspect = options['aspect']
    crop = ''
    if aspect != 'original':
        ratio = float(aspect.split(':')[0]) / float(aspect.split(':')[1])
        if width / height > ratio:
            width = int(height * ratio)
        else:
            height = int(width / ratio)
        width, height = width // 2 * 2, height // 2 * 2
        crop = f',crop={width}:{height}'
    width, height = width // 2 * 2, height // 2 * 2
    ass = work / 'captions.ass'
    ass.write_text(ass_document(data, width, height, start, end + tail, speed, entries), encoding='utf-8-sig')
    # Only fixed, validated paths enter the subtitle filter. FFmpeg runs from its work directory.
    import shutil
    for font in FONT_DIR.glob('*.ttf'):
        shutil.copy2(font, work / font.name)
    filters = [f'[0:v]trim=start={start}:end={end},setpts=(PTS-STARTPTS)/{speed}{crop},scale={width}:{height},fps={info["fps"]:.6f},tpad=stop_mode=clone:stop_duration={tail/speed}[base]']
    tag = 'base'
    if style['mask']:
        x, y = int(style['mask_x'] * width), int(style['mask_y'] * height)
        mw, mh = max(2, int(style['mask_width']*width)), max(2, int(style['mask_height']*height))
        if style['mask_blur']:
            radius = max(1, min(10, mw//3, mh//3))
            filters += [f'[base]split[bg][patch]', f'[patch]crop={mw}:{mh}:{x}:{y},boxblur={radius}:1[blur]', f'[bg][blur]overlay={x}:{y}[masked]']
        else:
            filters += [f'[base]drawbox=x={x}:y={y}:w={mw}:h={mh}:color=black:t=fill[masked]']
        tag = 'masked'
    filters.append(f'[{tag}]ass=filename=captions.ass:fontsdir=.[v]' if options.get('subtitles', True) else f'[{tag}]null[v]')
    args = ['-i', video]
    audio_map = []
    if voice_track:
        args += ['-i', voice_track]
        mix = cfg['mix']
        filters.append(f'[1:a]volume={mix["voice_volume"]}[voice]')
        if has_audio(video):
            filters.append(f'[0:a]volume={mix["bg_volume"]}[bg]')
            if mix['ducking']:
                filters += ['[voice]asplit[side][main]', '[bg][side]sidechaincompress=threshold=0.025:ratio=8[duck]', '[duck][main]amix=inputs=2:duration=longest:normalize=0[mix]']
            else:
                filters.append('[bg][voice]amix=inputs=2:duration=longest:normalize=0[mix]')
        else:
            filters.append('[voice]anull[mix]')
        filters.append(f'[mix]atrim=start={start}:end={end+tail},asetpts=PTS-STARTPTS,{atempo(speed)},alimiter=limit=0.95[a]')
        audio_map = ['-map', '[a]', '-c:a', 'aac']
    elif has_audio(video):
        filters.append(f'[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,{atempo(speed)}[a]')
        audio_map = ['-map', '[a]', '-c:a', 'aac']
    args += ['-filter_complex', ';'.join(filters), '-map', '[v]', *audio_map, '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-pix_fmt', 'yuv420p', '-t', (end+tail-start)/speed, '-movflags', '+faststart', output]
    run(args, cancelled, cwd=work)
    if probe(output)['duration'] <= 0:
        raise ValueError('File xuất không hợp lệ')
    return {'start': start, 'end': end, 'speed': speed, 'width': width, 'height': height,
            'extended_seconds': tail/speed, 'duration': (end+tail-start)/speed}
