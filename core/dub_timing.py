"""Schedule spoken cues using measured audio lengths in source-video time."""


def schedule(cues, video_speed=1.0, max_speed=1.65, max_overlap=1.0, video_end=0.0):
    """Return cue timing without losing speech; overlap limits are output seconds."""
    ordered = sorted(cues, key=lambda cue: (float(cue['start']), float(cue['end']), str(cue['id'])))
    result = {}
    previous = []
    overlap_source = max_overlap * video_speed
    # A full second of simultaneous speech is hard to understand. Spend only a
    # short overlap by default; use the configured maximum to contain backlog.
    preferred_overlap = min(max_overlap, .2) * video_speed
    delay_tolerance = .5 * video_speed
    preferred_speed = min(max_speed, 1.3)
    for index, cue in enumerate(ordered):
        original = float(cue['start'])
        start = original
        if previous:
            excess = previous[-1]['play_end'] - original
            allowed_overlap = min(overlap_source, max(preferred_overlap, excess - delay_tolerance))
            start = max(start, previous[-1]['play_end'] - allowed_overlap)
        if len(previous) > 1:
            start = max(start, previous[-2]['play_end'])
        next_start = float(ordered[index + 1]['start']) if index + 1 < len(ordered) else video_end
        target_end = max(float(cue['end']), next_start)
        available = max(.001, target_end - start)
        # First fit without overlap at a modest rate. A short shared tail can
        # then avoid rushing the voice; longer overlap is a hard fallback only.
        if index + 1 < len(ordered):
            needed = float(cue['duration']) * video_speed / preferred_speed - available
            available += min(preferred_overlap, max(0.0, needed))
        speed = min(max_speed, max(1.0, float(cue['duration']) * video_speed / available))
        play_end = start + float(cue['duration']) * video_speed / speed
        item = dict(play_start=start, play_end=play_end, speed=speed,
                    delay=max(0.0, (start - original) / video_speed),
                    overlap=max(0.0, (previous[-1]['play_end'] - start) / video_speed) if previous else 0.0)
        result[cue['id']] = item
        previous.append(item)
    return result


def captions(segments, config, entries=None):
    """Caption windows in source-video time, switching midway through overlapping speech."""
    entries = entries or {}
    dubbed = config['render']['dubbed']
    limit = config['caption_hold'] * config['render']['speed']
    ordered = sorted((seg for seg in segments if not seg.get('no_dialogue')),
                     key=lambda seg: (seg['start'], seg['end'], seg['id']))
    speech = []
    for seg in ordered:
        entry = entries.get(seg['id'], {}) if dubbed else {}
        if entry.get('status') != 'ready':
            entry = {}
        speech.append((float(entry.get('play_start', seg['start'])),
                       float(entry.get('play_end', seg['end']))))
    result = {}
    for index, seg in enumerate(ordered):
        start, end = speech[index]
        if dubbed and index:
            previous_end = speech[index - 1][1]
            if previous_end > start:
                start = (previous_end + start) / 2
        if index + 1 < len(ordered):
            next_start = speech[index + 1][0]
            end = min(end, (end + next_start) / 2 if dubbed and end > next_start else next_start)
        result[seg['id']] = (start, max(start, min(end, start + limit)))
    return result
