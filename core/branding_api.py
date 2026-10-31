"""Studio-owned branding endpoints. No caller supplied filesystem paths."""
import asyncio
import copy
from pathlib import Path
from fastapi import Request, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from . import branding, studio_store as store, studio_media as media

class Preview(BaseModel):
    filepath: str
    branding: dict
    aspect: str = 'original'

class Preset(BaseModel):
    filepath: str
    name: str
    branding: dict

class Rename(BaseModel):
    name: str = Field(min_length=1,max_length=80)

class Apply(BaseModel):
    filepath: str
    expected_revision: int = Field(ge=0)


def attach(router, service, call):
    library=branding.BrandLibrary(Path(service.host.DATA_DIR)/'.runtime'/'branding')
    def project_dir(filepath):
        video=service.resolve(filepath)
        directory=Path(service.check(store.paths(video)[2]/'branding'))
        return video,branding.safe_directory(directory)

    @router.post('/branding/logo')
    async def upload(request: Request, filepath: str, filename: str):
        content=bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content)>branding.MAX_UPLOAD_BYTES:raise HTTPException(400,f'Logo tối đa {branding.MAX_UPLOAD_MB} MB')
        def process():
            _,directory=project_dir(filepath)
            return {'image':branding.upload(directory,bytes(content),filename)}
        return await asyncio.to_thread(call,process)

    @router.get('/branding/image/{image_id}')
    def image(image_id: str, filepath: str):
        def locate():
            _,directory=project_dir(filepath)
            path=branding.asset_path(directory,image_id)
            if not path.is_file():raise FileNotFoundError('Không tìm thấy ảnh thương hiệu')
            return path
        return FileResponse(call(locate),media_type='image/png',headers={'Cache-Control':'private, max-age=31536000, immutable'})

    @router.get('/branding/frame')
    def frame(filepath: str, at: float = Query(default=0,ge=0,allow_inf_nan=False), aspect: str = 'original'):
        def process():
            video = service.resolve(filepath)
            return branding.preview_frame(video,aspect,at)
        return Response(content=call(process),media_type='image/jpeg',headers={'Cache-Control':'no-store'})

    @router.post('/branding/preview')
    def preview(req: Preview):
        def process():
            video,directory=project_dir(req.filepath)
            data=store.load(video)
            config=branding.validate(req.branding,media.probe(video)['duration'])
            if req.aspect not in ('original','9:16','16:9','1:1'):raise ValueError('Tỷ lệ khung hình không hợp lệ')
            w,h=branding.dimensions(media.probe(video),req.aspect)
            return {'layers':branding.layers(config,directory,w,h),'width':w,'height':h}
        return call(process)

    @router.get('/branding/presets')
    def presets():return call(lambda:{'presets':library.listing()})

    @router.post('/branding/presets')
    def create(req: Preset):
        return call(lambda:library.save(req.name,req.branding,project_dir(req.filepath)[1]))

    @router.put('/branding/presets/{key}')
    def update(key: str,req: Preset):
        return call(lambda:library.save(req.name,req.branding,project_dir(req.filepath)[1],key))

    @router.patch('/branding/presets/{key}')
    def rename(key: str,req: Rename):
        def process():
            path=library.preset_path(key)
            if not path.exists():raise FileNotFoundError('Không tìm thấy bộ thương hiệu')
            data=store.read_json(path,{})
            if not req.name.strip():raise ValueError('Nhập tên bộ thương hiệu')
            data['name']=req.name.strip();store.atomic_json(path,data)
            return data
        return call(process)

    @router.delete('/branding/presets/{key}')
    def delete(key: str):
        def process():library.preset_path(key).unlink();return {'status':'deleted'}
        return call(process)

    @router.post('/branding/presets/{key}/apply')
    def apply(key: str,req: Apply):
        def process():
            with store.LOCK:
                video,directory=project_dir(req.filepath)
                data=store.load(video)
                if data['revision']!=req.expected_revision:raise store.Conflict('Dự án đã đổi; tải lại trước khi áp dụng')
                preset=library.load(key,directory)
                data['studio_config']['branding']=copy.deepcopy(preset['branding'])
                return service.save(req.filepath,req.expected_revision,data)
        return call(process)
