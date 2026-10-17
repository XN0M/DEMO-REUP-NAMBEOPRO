"""Moves one source and its assets without overwriting existing files."""
import os
from .video_files import associated_files, is_source

def move_video(source, root, destination, validate):
    parent = os.path.dirname(source)
    names = os.listdir(parent)
    sources = [n for n in names if is_source(n, names)]
    # Never move a collection directory or the download root itself.
    relative = os.path.relpath(parent, root)
    import re
    private = (len(sources) == 1 and sources[0] == os.path.basename(source) and
               (os.sep in relative or re.fullmatch(r'[a-zA-Z]+_\d{3,}_\d{8}(?:_\d+)?', relative)))
    if private:
        for folder, dirs, files in os.walk(parent, followlinks=False):
            for name in dirs + files:
                validate(os.path.relpath(os.path.join(folder, name), root))
    assets = [parent] if private else associated_files(source)
    for asset in assets:
        validate(os.path.relpath(asset, root))
    folder_name = os.path.basename(parent) if private else 'video_' + os.path.splitext(os.path.basename(source))[0][:60]
    os.makedirs(destination, exist_ok=True)
    target = os.path.join(destination, folder_name)
    index = 2
    while os.path.lexists(target):
        target = os.path.join(destination, f'{folder_name}_{index}')
        index += 1
    validate(os.path.relpath(target, root))
    if private:
        os.rename(parent, target)
        return os.path.join(target, os.path.basename(source))
    os.mkdir(target)
    moved = []
    try:
        for asset in assets:
            new_path = os.path.join(target, os.path.basename(asset))
            os.rename(asset, new_path)
            moved.append((asset, new_path))
    except Exception as error:
        rollback_errors = []
        for old, new in reversed(moved):
            try:
                os.rename(new, old)
            except Exception as rollback:
                rollback_errors.append(f'{new} -> {old}: {rollback}')
        if not rollback_errors:
            os.rmdir(target)
        raise RuntimeError(f'{error}' + ('; Phục hồi chưa hoàn tất: ' + '; '.join(rollback_errors) if rollback_errors else '')) from error
    return os.path.join(target, os.path.basename(source))
