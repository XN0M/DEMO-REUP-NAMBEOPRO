"""FastAPI adapter; all paths resolve through the host application's sandbox."""
import copy
import os
import subprocess
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from . import studio_store as store, studio_media as media
from .studio_service import StudioService

class Save(BaseModel):
    filepath: str
    expected_revision: int = Field(ge=0)
    project: dict = Field(default_factory=dict)
    restore_revision: int | None = Field(default=None, ge=0)

class Job(BaseModel):
    filepath: str
    revision: int = Field(ge=0)
    segment_ids: list[str] | None = None
    at: float = Field(default=0, ge=0, allow_inf_nan=False)
    skip_errors: bool = False

class Transcribe(Job):
    model_size: str = Field(default='base', pattern='^(tiny|base|small)$')
    language: str = Field(default='auto', pattern='^(auto|[a-z]{2,3})$')

class ApplyTranscript(BaseModel):
    expected_revision: int = Field(ge=0)

def attach(host, database=None):
    service = StudioService(host, database)
    router = APIRouter(prefix='/api/studio')

    @host.app.exception_handler(RequestValidationError)
    async def invalid_request(request,error):
        if request.url.path.startswith('/api/studio/'):
            return JSONResponse(status_code=400,content={'detail':'Dữ liệu Studio không hợp lệ',
                'errors':[{'loc':list(e['loc']),'msg':e['msg']} for e in error.errors()]})
        return await request_validation_exception_handler(request,error)

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except store.Conflict as error:
            raise HTTPException(409, str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(404, str(error)) from error
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(400, str(error)) from error

    @router.get('/project')
    def project(filepath: str):
        return call(service.project, filepath)

    @router.put('/project')
    def save(req: Save):
        return call(service.save, req.filepath, req.expected_revision, req.project, req.restore_revision)

    def launch(kind, req):
        return call(service.launch, kind, req.filepath, req.revision, req.segment_ids, at=req.at, skip_errors=req.skip_errors)

    @router.post('/tts')
    def tts(req: Job):
        return launch('tts',req)

    @router.post('/transcribe')
    def transcribe(req: Transcribe):
        return call(service.launch, 'transcribe', req.filepath, req.revision,
                    model_size=req.model_size, language=req.language)

    @router.post('/jobs/{jid}/apply-transcript')
    def apply_transcript(jid: str, req: ApplyTranscript):
        return call(service.apply_transcript, jid, req.expected_revision)

    @router.post('/translate')
    def translate(req: Job):
        return launch('translate',req)

    @router.post('/character-map')
    def character_map(req: Job):
        return launch('character-map', req)

    @router.post('/shorten')
    def shorten(req: Job):
        return launch('shorten',req)

    @router.post('/preview')
    def preview(req: Job):
        return launch('preview',req)

    @router.post('/export')
    def export(req: Job):
        return launch('export',req)

    @router.post('/waveform')
    def waveform(req: Job):
        video = call(service.resolve,req.filepath)
        if not media.has_audio(video):
            return {'status':'completed','peaks':[]}
        return launch('waveform',req)

    @router.get('/jobs/{jid}')
    def job(jid: str):
        result = call(service.get,jid)
        # No full transcript in polling responses; show whether the result is stale now.
        try:
            result['stale'] = store.load(service.resolve(result['filepath']))['revision'] != result['revision']
        except (ValueError, HTTPException):
            result['stale'] = True
        result.pop('snapshot',None)
        return result

    @router.post('/jobs/{jid}/cancel')
    def cancel(jid: str):
        return call(service.cancel,jid)

    @router.post('/jobs/{jid}/open-folder')
    def open_result_folder(jid: str):
        job = call(service.get, jid)
        video = call(service.resolve, job['filepath'])
        assets = store.paths(video)[2].resolve()
        target = Path(call(service.check, assets / job.get('artifact', ''))).resolve()
        if assets not in target.parents or target.suffix != '.mp4' or not target.is_file():
            raise HTTPException(404, 'Chưa có kết quả xuất')
        try:
            if os.name == 'nt':
                os.startfile(str(target.parent))
            else:
                subprocess.Popen(['xdg-open', str(target.parent)])
        except OSError as error:
            raise HTTPException(400, 'Không mở được thư mục kết quả') from error
        return {'status': 'success'}

    @router.get('/jobs')
    def jobs(filepath: str):
        service.recover()
        with service.db() as con:
            rows = con.execute("SELECT data FROM jobs WHERE json_extract(data, '$.filepath')=? ORDER BY rowid DESC LIMIT 20", (filepath,)).fetchall()
        import json
        results = [json.loads(row[0]) for row in rows]
        return {'jobs':[{k:v for k,v in j.items() if k != 'snapshot'} for j in results if j['filepath']==filepath][:20]}

    @router.get('/asset')
    def asset(filepath: str, name: str):
        video = call(service.resolve,filepath)
        root = store.paths(video)[2].resolve()
        target = Path(service.check(root / name)).resolve()
        if root not in target.parents or target.suffix not in ('.wav','.mp4') or not target.is_file():
            raise HTTPException(404,'Không tìm thấy media Studio')
        return FileResponse(target, media_type='audio/wav' if target.suffix=='.wav' else 'video/mp4')

    @router.get('/subtitles')
    def subtitles(filepath: str, format: str = Query(default='ass', pattern='^(ass|srt)$')):
        video = call(service.resolve,filepath)
        data = store.load(video)
        info = media.probe(video)
        options = data['studio_config']['render']
        start, end, speed = options['start'], options['end'] or info['duration'], options['speed']
        if format=='ass':
            w,h=info['width'],info['height']
            if options['aspect']!='original':
                a,b=map(float,options['aspect'].split(':'))
                w,h=(int(h*a/b),h) if w/h>a/b else (w,int(w*b/a))
            content=media.ass_document(data,w//2*2,h//2*2,start,end,speed)
        else:
            from .transcriber import DouyinTranscriber
            segments=[]
            for seg in data['segments']:
                a,b=max(start,seg['start']),min(end,seg['end'])
                if b>a:
                    text=seg.get('vietnamese') or store.source_text(seg)
                    if data['studio_config']['style']['bilingual'] and seg.get('vietnamese'):
                        pair=[seg['vietnamese'],store.source_text(seg)]
                        if not data['studio_config']['style']['translated_first']: pair.reverse()
                        text='\n'.join(pair)
                    segments.append(dict(start=(a-start)/speed,end=(b-start)/speed,text=text))
            content=DouyinTranscriber.generate_srt(segments,'text')
        return Response(content,media_type='text/plain; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="captions.{format}"'})

    host.app.include_router(router)
    @host.app.on_event('startup')
    def recover():
        service.recover()
    return service
