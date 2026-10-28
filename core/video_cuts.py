"""Validate and export independent video segments without blocking the API."""
import asyncio
import math
import os
from datetime import datetime
from uuid import uuid4


def validate_segments(segments, info):
    if not segments:
        raise ValueError('Danh sách đoạn không được rỗng')
    result = []
    previous_end = 0.0
    minimum = 1 / info['fps']
    for item in segments:
        start, end = float(item['start']), float(item['end'])
        if (not math.isfinite(start) or not math.isfinite(end) or start < 0 or
                end > info['duration'] + 1e-6 or start < previous_end or
                end - start < minimum - 1e-6):
            raise ValueError('Mốc cắt phải trong video, đúng thứ tự, không chồng lấn và dài ít nhất một khung hình')
        result.append({'start': start, 'end': min(end, info['duration'])})
        previous_end = end
    return result


async def execute_cuts(editor, task_id, source, segments, crop=None, lossless=False):
    task = editor.tasks[task_id]
    task.update(status='processing', progress_text='Đang chuẩn bị...', outputs=[])

    def run():
        stem = os.path.splitext(os.path.basename(source))[0][:100]
        folder = os.path.join(os.path.dirname(source),
                              f'{stem}_cuts_{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:8]}')
        os.makedirs(folder)
        task['output_folder'] = folder
        total = sum(item['end'] - item['start'] for item in segments)
        done = 0
        for index, segment in enumerate(segments, 1):
            task['current_segment'] = index
            task['total_segments'] = len(segments)
            path = os.path.join(folder, f'part_{index:03d}.mp4')
            temp = os.path.join(folder, f'.part_{index:03d}.{uuid4().hex}.tmp')
            length = segment['end'] - segment['start']

            def progress(percent, message):
                task['percent'] = round((done + length * percent / 100) / total * 100, 1)
                task['progress_text'] = f'Đang xuất đoạn {index}/{len(segments)}: {message}'

            try:
                editor.crop_trim_video(source, temp, crop=crop, trim=segment,
                                       lossless_trim=lossless, progress_callback=progress)
                os.replace(temp, path)
                task['outputs'].append({'index': index, **segment, 'path': path})
            finally:
                if os.path.exists(temp):
                    os.remove(temp)
            done += length

    try:
        await asyncio.to_thread(run)
        task.update(status='completed', percent=100, progress_text='Đã xuất tất cả các đoạn')
    except Exception as exc:
        task.update(status='error', error=f"Đoạn {task.get('current_segment', 1)}: {exc}",
                    progress_text='Xuất lỗi; các đoạn đã hoàn tất vẫn được giữ')
