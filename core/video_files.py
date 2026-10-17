"""Shared source/companion recognition for history, moves and deletion."""
import os
import re

MEDIA_SUFFIXES = tuple(sorted((
    '.dubbed.mp4', '.final.mp4', '.cropped.mp4', '.backup.mp4',
    '.hardsub.vi.mp4', '.hardsub.bilingual.mp4',
    '.softsub.vi.mp4', '.softsub.bilingual.mp4',
    '.dubbed.hardsub.vi.mp4', '.dubbed.hardsub.bilingual.mp4',
    '.dubbed.softsub.vi.mp4', '.dubbed.softsub.bilingual.mp4',
), key=len, reverse=True))
COMPANION_SUFFIXES = MEDIA_SUFFIXES + (
    '.json', '.thumb.jpg', '.transcript.json', '.transcript.txt',
    '.studio.json', '.srt', '.vi.srt', '.bilingual.srt', '.dubbed.vi.srt', '.dubbed.bilingual.srt',
    '.dubbed.mp3', '.accompaniment.mp3',
)

def is_source(name, names):
    if not name.lower().endswith('.mp4') or re.search(r'\.f\d+\.mp4$', name):
        return False
    lowered = {n.lower() for n in names}
    return not any(name.lower().endswith(s) and
                   name[:-len(s)].lower() + '.mp4' in lowered for s in MEDIA_SUFFIXES)

def associated_files(path):
    base = path[:-4]
    return [p for p in [path] + [base + s for s in COMPANION_SUFFIXES] + [base + '.studio-assets'] if os.path.exists(p)]

def collection_for(path, root):
    first = os.path.relpath(os.path.dirname(path), root).split(os.sep)[0]
    return 'Chung' if first == '.' or re.fullmatch(r'[a-zA-Z]+_\d{3,}_\d{8}(?:_\d+)?', first) else first
