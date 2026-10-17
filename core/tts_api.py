"""Public, host-owned APIs for local speech configuration and voice uploads."""
import asyncio
from fastapi import APIRouter, HTTPException, Request, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from .tts_runtime import runtime, library, TTSError


class Rename(BaseModel):
    name: str


class Device(BaseModel):
    device: str


def attach(host):
    router = APIRouter(prefix='/api/tts')

    @router.get('/status')
    async def status():
        return await asyncio.to_thread(runtime.status)

    @router.post('/install')
    async def install():
        return await asyncio.to_thread(runtime.install)

    @router.put('/device')
    async def device(req: Device):
        try:
            await asyncio.to_thread(runtime.set_device, req.device)
            return runtime.status()
        except TTSError as error:
            raise HTTPException(400, str(error))

    @router.get('/log')
    def log(kind: str = 'worker'):
        if kind not in ('install', 'worker'):
            raise HTTPException(400, 'Loại nhật ký không hợp lệ')
        path = runtime.directory / (kind + '.log')
        if not path.exists():
            return PlainTextResponse('Chưa có nhật ký.')
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 32000))
            return PlainTextResponse(stream.read().decode('utf-8', errors='replace'))

    @router.get('/voices/custom')
    def voices():
        return {'voices': library.list()}

    @router.post('/voices/custom')
    async def upload(request: Request, name: str = Query(...), filename: str = Query(...), consent: bool = False):
        content = bytearray()
        async for block in request.stream():
            content.extend(block)
            if len(content) > 20 * 1024 * 1024:
                raise HTTPException(413, 'File mẫu không quá 20 MB')
        try:
            return await asyncio.to_thread(library.create, name, filename, bytes(content), consent)
        except (TTSError, ValueError) as error:
            raise HTTPException(400, str(error))

    @router.patch('/voices/custom/{identity}')
    def rename(identity: str, req: Rename):
        try:
            return library.rename(identity, req.name)
        except TTSError as error:
            raise HTTPException(400, str(error))

    @router.delete('/voices/custom/{identity}')
    def delete(identity: str):
        try:
            library.delete(identity)
            return {'status': 'deleted'}
        except TTSError as error:
            raise HTTPException(409, str(error))

    @router.get('/voices/custom/{identity}/sample')
    def sample(identity: str):
        try:
            return FileResponse(library.sample(identity), media_type='audio/wav')
        except TTSError as error:
            raise HTTPException(404, str(error))

    host.app.include_router(router)
