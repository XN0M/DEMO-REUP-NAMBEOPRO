"""Reviewable cleanup plans. Never remove unknown files or traverse links."""
import hashlib
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from . import studio_store as store, studio_media as media


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


class StudioCleanup:
    def __init__(self, service):
        self.s = service
        self.directory = Path(service.database).parent / 'cleanup'
        self.directory.mkdir(parents=True, exist_ok=True)

    def safe(self, path):
        path = Path(path).absolute()
        if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()) for p in (path, *path.parents)):
            raise ValueError('Không dọn đường dẫn liên kết')
        checked = Path(self.s.check(path))
        if checked.resolve() != path.resolve():
            raise ValueError('Đường dẫn không hợp lệ')
        return path

    def files(self, video):
        transcript, mirror, assets = store.paths(video)
        result = [Path(video), transcript, mirror]
        if assets.exists():
            for folder, dirs, files in os.walk(assets, followlinks=False):
                for name in dirs:
                    self.safe(Path(folder) / name)
                for name in files:
                    p = self.safe(Path(folder) / name)
                    rel = p.relative_to(assets)
                    # Only Studio-owned names; leave unknown assets untouched.
                    if (len(rel.parts) == 1 and (name in ('audio.json', 'original.transcript.json') or name.startswith(('preview-r', 'export-r')) and p.suffix == '.mp4')
                        or rel.parts[0] == 'branding' and re.fullmatch(r'[0-9a-f]{64}\.png', name)
                        or rel.parts[0] == 'versions' and p.suffix == '.json'
                        or rel.parts[0] == 'audio' and re.fullmatch(r'[0-9a-f]{64}(?:-[0-9a-f]{16}-[0-9a-f]{16})?\.wav', name)
                        or rel.parts[0] == 'jobs' and name in ('voice.wav', 'wave.wav', 'output.mp4', 'subtitles.ass', 'captions.ass', 'brand-logo.png', 'brand-text.png', 'DejaVuSans.ttf', 'DejaVuSans-Bold.ttf')):
                        result.append(p)
        return [self.safe(p) for p in result if p.is_file()]

    def plan(self, filepath, jid, mode):
        if mode not in ('compact', 'output_only'):
            raise ValueError('Mức dọn không hợp lệ')
        video = self.s.resolve(filepath)
        with store.LOCK:
            if self.s.host._download_in_progress(video) or not self.s.host._reserve_source_job(video):
                raise store.Conflict('Video đang được xử lý')
            try:
                data = store.load(video)
                job = self.s.get(jid)
                if job['filepath'] != filepath or job['kind'] != 'export' or job['status'] != 'completed':
                    raise ValueError('Chọn bản xuất đầy đủ đã hoàn tất')
                assets = store.paths(video)[2]
                output = self.safe(assets / job['artifact'])
                media.probe(output)
                candidates = self.files(video)
                # Protect raw audio referenced by the current project, all saved
                # versions and prior job snapshots, even when they are not active.
                protected = set()
                snapshots = [data]
                for version in (assets / 'versions').glob('*.json'):
                    snapshots.append(store.normalize(store.read_json(self.safe(version), {})))
                with self.s.db() as con:
                    for raw, in con.execute('SELECT data FROM jobs'):
                        previous = json.loads(raw)
                        if previous.get('filepath') == filepath and previous.get('snapshot'):
                            snapshots.append(store.normalize(previous['snapshot']))
                for snapshot in snapshots:
                    for seg in snapshot.get('segments', []):
                        key = store.audio_key(snapshot, seg, self.s.fingerprint(video))
                        protected.add(key + '.wav')
                        cue_key = hashlib.sha256(seg['id'].encode('utf-8')).hexdigest()[:16]
                        protected.add(f'{key}-{cue_key}-{store.timing_key(snapshot)[:16]}.wav')
                from . import branding
                brand_keys = set()
                for snapshot in snapshots:
                    brand = branding.normalize(snapshot.get('studio_config', {}).get('branding', {}))
                    if brand['logo']['image']:
                        brand_keys.add(brand['logo']['image'])
                    # Calculate hashes without creating files while planning cleanup.
                    if mode == 'compact' and any(layer['enabled'] for layer in brand.values()):
                        width, height = branding.dimensions(media.probe(video), snapshot['studio_config']['render']['aspect'])
                        brand_keys.update(layer['image'] for layer in branding.layers(brand, assets/'branding',width,height,write=False))
                candidates = self.files(video)
                manifest = self.s.manifest(video)
                protected_aligned = {e.get('aligned') for e in manifest.values()}
                remove = []
                for p in candidates:
                    if p == output:
                        continue
                    if mode == 'output_only':
                        remove.append(p)
                    elif p.parent == assets / 'branding' and p.stem not in brand_keys:
                        remove.append(p)
                    elif assets in p.parents and p.parent == assets / 'audio' and p.name not in protected and str(p.relative_to(assets)).replace('\\', '/') not in protected_aligned:
                        remove.append(p)
                    elif assets in p.parents and (p.name.startswith(('preview-r', 'export-r')) or 'jobs' in p.relative_to(assets).parts):
                        remove.append(p)
                identity = uuid.uuid4().hex
                plan = dict(id=identity, filepath=filepath, job_id=jid, mode=mode, revision=data['revision'],
                            output=str(output), output_hash=digest(output), status='planned', deleted=[], errors=[],
                            files=[dict(path=str(p), size=p.stat().st_size, mtime=p.stat().st_mtime_ns) for p in candidates],
                            remove=[str(p) for p in remove], bytes=sum(p.stat().st_size for p in remove))
                store.atomic_json(self.directory / (identity + '.json'), plan)
                return self.public(plan)
            finally:
                self.s.host._release_source_job(video)

    @staticmethod
    def public(plan):
        return {k: plan[k] for k in ('id', 'mode', 'revision', 'status', 'bytes', 'remove', 'deleted', 'errors')}

    def execute(self, identity, confirmed):
        if not confirmed or len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('Cần xác nhận phương án dọn')
        journal = self.directory / (identity + '.json')
        plan = store.read_json(journal, {})
        if not plan:
            raise ValueError('Không tìm thấy phương án')
        video = str(self.safe(self.s.host._resolve_safe_path(plan['filepath'])))
        with store.LOCK:
            if not self.s.host._reserve_source_job(video):
                raise store.Conflict('Video đang được xử lý')
            try:
                if plan['status'] == 'completed':
                    return self.public(plan)
                if plan['status'] != 'planned' and store.paths(video)[0].exists() and store.load(video)['revision'] != plan['revision']:
                    raise store.Conflict('Dự án đã đổi; không tiếp tục phương án cũ')
                if plan['status'] == 'planned':
                    if self.s.host._download_in_progress(video) or store.load(video)['revision'] != plan['revision']:
                        raise store.Conflict('Dự án thay đổi; lập lại phương án')
                    for item in plan['files']:
                        stat = self.safe(item['path']).stat()
                        if (stat.st_size, stat.st_mtime_ns) != (item['size'], item['mtime']):
                            raise store.Conflict('File thay đổi; lập lại phương án')
                    if digest(self.safe(plan['output'])) != plan['output_hash']:
                        raise store.Conflict('Bản xuất thay đổi')
                if plan['mode'] == 'output_only':
                    destination = self.safe(Path(self.s.host.current_download_dir) / 'finished' / ('finished-' + identity + '.mp4'))
                    destination.parent.mkdir(exist_ok=True)
                    if not destination.exists():
                        temporary = destination.with_suffix('.part')
                        shutil.copyfile(self.safe(plan['output']), temporary)
                        if digest(temporary) != plan['output_hash']:
                            raise ValueError('Sao chép thành phẩm không toàn vẹn')
                        media.probe(temporary)
                        os.replace(temporary, destination)
                    if digest(destination) != plan['output_hash']:
                        raise ValueError('Thành phẩm không toàn vẹn; giữ nguyên dự án')
                    # Library discovers MP4 files under the download root.
                    store.atomic_json(destination.with_suffix('.json'), {'title': 'Chỉ còn thành phẩm', 'platform': 'studio', 'output_only': True})
                    if plan['output'] not in plan['remove']:
                        plan['remove'].append(plan['output'])
                    plan['destination'] = str(destination)
                plan['status'] = 'deleting'
                store.atomic_json(journal, plan)
                plan['errors'] = []
                freed = 0
                for name in plan['remove']:
                    if name in plan['deleted']:
                        continue
                    p = self.safe(name)
                    try:
                        previous = next((f for f in plan['files'] if f['path'] == name), None)
                        if p.exists() and previous and (p.stat().st_size, p.stat().st_mtime_ns) != (previous['size'], previous['mtime']):
                            plan['errors'].append(name)
                            continue
                        size = p.stat().st_size if p.exists() else 0
                        p.unlink(missing_ok=True)
                        freed += size
                        plan['deleted'].append(name)
                        plan['freed_bytes'] = plan.get('freed_bytes', 0) + size
                        store.atomic_json(journal, plan)
                    except OSError:
                        plan['errors'].append(name)
                plan['bytes'] = plan.get('freed_bytes', freed)
                plan['status'] = 'partial' if plan['errors'] else 'completed'
                store.atomic_json(journal, plan)
                return self.public(plan)
            finally:
                self.s.host._release_source_job(video)


def clean_temporary(work):
    """Only run after subprocesses have stopped. Preserve unknown files."""
    import logging
    for path in work.iterdir() if work.exists() else []:
        if path.is_symlink() or not path.is_file():
            continue
        if path.name in ('voice.wav', 'wave.wav', 'output.mp4', 'subtitles.ass', 'captions.ass', 'brand-logo.png', 'brand-text.png', 'DejaVuSans.ttf', 'DejaVuSans-Bold.ttf') or re.fullmatch(r'[0-9a-f-]{32,36}(?:-ref)?\.(?:wav|audio)', path.name):
            try:
                path.unlink()
            except OSError:
                logging.getLogger(__name__).warning('Studio temporary file remains locked')
