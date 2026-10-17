"""Schedule spoken cues using measured audio lengths in source-video time."""


def schedule(cues, video_speed=1.0, max_speed=1.65, max_overlap=1.0, video_end=0.0):
    """Return cue timing without losing speech; overlap limits are output seconds."""
    ordered = sorted(cues, key=lambda cue: (float(cue['start']), float(cue['end']), str(cue['id'])))
    result = {}
    previous = []
    overlap_source = max_overlap * video_speed
    for index, cue in enumerate(ordered):
        original = float(cue['start'])
        start = original
        if previous:
            start = max(start, previous[-1]['play_end'] - overlap_source)
        if len(previous) > 1:
            start = max(start, previous[-2]['play_end'])
        next_start = float(ordered[index + 1]['start']) if index + 1 < len(ordered) else video_end
        target_end = max(float(cue['end']), next_start)
        # Spending a small overlap can preserve natural speech before speeding it up.
        available = max(.001, target_end + (overlap_source if index + 1 < len(ordered) else 0) - start)
        speed = min(max_speed, max(1.0, float(cue['duration']) * video_speed / available))
        play_end = start + float(cue['duration']) * video_speed / speed
        item = dict(play_start=start, play_end=play_end, speed=speed,
                    delay=max(0.0, (start - original) / video_speed),
                    overlap=max(0.0, (previous[-1]['play_end'] - start) / video_speed) if previous else 0.0)
        result[cue['id']] = item
        previous.append(item)
    return result


def captions(segments, config, entries=None):
    """Caption windows in source-video time, ordered and clipped at the next cue."""
    entries = entries or {}
    dubbed = config['render']['dubbed']
    limit = config['caption_hold'] * config['render']['speed']
    ordered = sorted(segments, key=lambda seg: (seg['start'], seg['end'], seg['id']))
    result = {}
    for index, seg in enumerate(ordered):
        entry = entries.get(seg['id'], {}) if dubbed else {}
        if entry.get('status') != 'ready':
            entry = {}
        start = float(entry.get('play_start', seg['start']))
        end = float(entry.get('play_end', seg['end']))
        next_seg = ordered[index + 1] if index + 1 < len(ordered) else None
        if next_seg:
            following = entries.get(next_seg['id'], {}) if dubbed else {}
            if following.get('status') != 'ready':
                following = {}
            end = min(end, float(following.get('play_start', next_seg['start'])))
        result[seg['id']] = (start, max(start, min(end, start + limit)))
    return result
