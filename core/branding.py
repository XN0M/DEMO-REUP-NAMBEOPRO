"""Validated, immutable branding assets and shared preview/render geometry."""
import copy
import hashlib
import io
import json
import math
import os
import re
import uuid
import warnings
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

FONT_DIR = Path(__file__).resolve().parents[1] / 'web' / 'fonts'
COMMON = dict(enabled=False, x=.03, y=.03, width=.3, opacity=.7, start=None, end=None)
DEFAULT = dict(logo=dict(COMMON, image=None, x=.85, width=.12, opacity=.8),
               text=dict(COMMON, content='', bold=False, color='#FFFFFF', outline_color='#000000', outline=1, size=32))
ID = re.compile(r'[0-9a-f]{64}')

def identity(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('ID ảnh thương hiệu không hợp lệ')
    return value

def normalize(value):
    if not isinstance(value, dict) or set(value)-set(DEFAULT):
        raise ValueError('Cấu hình thương hiệu không hợp lệ')
    result = copy.deepcopy(DEFAULT)
    for kind, layer in value.items():
        if not isinstance(layer, dict) or set(layer)-set(result[kind]):
            raise ValueError('Thành phần thương hiệu không hợp lệ')
        result[kind].update(layer)
    return result

def validate(value, duration=None):
    result = normalize(value)
    def number(v, low, high):
        if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError('Thông số thương hiệu ngoài phạm vi')
        return float(v)
    for kind, layer in result.items():
        if type(layer['enabled']) is not bool:
            raise ValueError('Trạng thái thương hiệu không hợp lệ')
        for key, low, high in [('x',0,1),('y',0,1),('width',.02,1),('opacity',0,1)]:
            layer[key] = number(layer[key],low,high)
        if (layer['start'] is None) != (layer['end'] is None):
            raise ValueError('Nhập đủ thời điểm đầu và cuối')
        if layer['start'] is not None:
            layer['start'] = number(layer['start'],0,duration if duration is not None else 864000)
            layer['end'] = number(layer['end'],0,duration if duration is not None else 864000)
            if layer['end'] <= layer['start']:
                raise ValueError('Khoảng hiển thị thương hiệu không hợp lệ')
        if kind == 'logo':
            if layer['image'] is not None: identity(layer['image'])
            if layer['enabled'] and not layer['image']:
                raise ValueError('Chọn ảnh logo trước khi bật')
        else:
            if not isinstance(layer['content'], str) or len(layer['content']) > 200 or any(ord(c)<32 and c not in '\n\t' for c in layer['content']):
                raise ValueError('Watermark chữ cần tối đa 200 ký tự hợp lệ')
            if layer['enabled'] and not layer['content'].strip():
                raise ValueError('Nhập nội dung watermark')
            if type(layer['bold']) is not bool: raise ValueError('Kiểu chữ không hợp lệ')
            for key in ('color','outline_color'):
                if not isinstance(layer[key],str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',layer[key]): raise ValueError('Màu chữ không hợp lệ')
            layer['size'] = number(layer['size'],12,120)
            layer['outline'] = number(layer['outline'],0,10)
    return result

def safe_directory(directory):
    directory = Path(directory).absolute()
    for p in (directory,*directory.parents):
        if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):
            raise ValueError('Không dùng liên kết cho tài nguyên thương hiệu')
    directory.mkdir(parents=True,exist_ok=True)
    return directory

def asset_path(directory, key):
    directory = safe_directory(directory)
    path = directory / (identity(key)+'.png')
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise ValueError('Đường dẫn ảnh thương hiệu không hợp lệ')
    return path

def png_bytes(image):
    data = io.BytesIO();image.save(data,format='PNG')
    return data.getvalue()

def put_png(directory, image):
    content = png_bytes(image);key = hashlib.sha256(content).hexdigest()
    target = asset_path(directory,key)
    if not target.exists():
        tmp = target.with_name(uuid.uuid4().hex+'.tmp')
        try:
            tmp.write_bytes(content);os.replace(tmp,target)
        finally: tmp.unlink(missing_ok=True)
    return key

def upload(directory, content, filename):
    suffix = Path(filename).suffix.lower()
    formats = {'.png':'PNG','.webp':'WEBP','.jpg':'JPEG','.jpeg':'JPEG'}
    if suffix not in formats or not content or len(content)>5*1024*1024:
        raise ValueError('Chọn PNG/WebP/JPG/JPEG tĩnh, tối đa 5 MB')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format != formats[suffix] or getattr(image,'n_frames',1)!=1 or not (0<image.width<=4096 and 0<image.height<=4096):
                    raise ValueError('Ảnh phải tĩnh, đúng định dạng và không quá 4096×4096')
                rgba = ImageOps.exif_transpose(image).convert('RGBA')
                rgba.info.clear()
                return put_png(directory,rgba)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError('Không đọc được ảnh logo') from error

def dimensions(info, aspect):
    width,height=info['width'],info['height']
    if aspect!='original':
        a,b=map(int,aspect.split(':'));ratio=a/b
        if width/height>ratio:width=int(height*ratio)
        else:height=int(width/ratio)
    return width//2*2,height//2*2

def text_image(layer, width, height):
    size=max(1,round(layer['size']*height/1080));stroke=round(layer['outline']*height/1080)
    font=ImageFont.truetype(str(FONT_DIR/('DejaVuSans-Bold.ttf' if layer['bold'] else 'DejaVuSans.ttf')),size)
    limit=max(1,round(layer['width']*width)-2*stroke-4)
    draw=ImageDraw.Draw(Image.new('RGBA',(1,1)))
    lines=[]
    for paragraph in layer['content'].replace('\t','    ').split('\n'):
        line=''
        for char in paragraph:
            if line and draw.textlength(line+char,font=font)>limit:
                lines.append(line);line=''
            line+=char
        lines.append(line)
    text='\n'.join(lines);bbox=draw.multiline_textbbox((0,0),text,font=font,stroke_width=stroke,spacing=round(size*.2))
    w=max(1,bbox[2]-bbox[0]+4);h=max(1,bbox[3]-bbox[1]+4)
    if w*h>16777216:raise ValueError('Watermark chữ quá lớn')
    image=Image.new('RGBA',(w,h))
    ImageDraw.Draw(image).multiline_text((2-bbox[0],2-bbox[1]),text,font=font,fill=layer['color'],stroke_fill=layer['outline_color'],stroke_width=stroke,spacing=round(size*.2))
    return image

def layers(config, directory, width, height, write=True):
    """Raster payloads and normalized geometry shared by browser and FFmpeg."""
    config=validate(config)
    result=[]
    for kind,layer in config.items():
        if not layer['enabled']:continue
        if kind=='logo':
            with Image.open(asset_path(directory,layer['image'])) as source:
                image=source.convert('RGBA');w=max(1,round(width*layer['width']));h=max(1,round(w*image.height/image.width))
                if h>height:w=max(1,round(w*height/h));h=height
                image=image.resize((w,h),Image.Resampling.LANCZOS)
        else:image=text_image(layer,width,height)
        if image.width>width or image.height>height:
            ratio=min(width/image.width,height/image.height)
            image=image.resize((max(1,int(image.width*ratio)),max(1,int(image.height*ratio))),Image.Resampling.LANCZOS)
        key=put_png(directory,image) if write else hashlib.sha256(png_bytes(image)).hexdigest()
        x=min(round(layer['x']*width),width-image.width);y=min(round(layer['y']*height),height-image.height)
        result.append(dict(kind=kind,image=key,x=x/width,y=y/height,width=image.width/width,height=image.height/height,opacity=layer['opacity'],start=layer['start'],end=layer['end']))
    return result

class BrandLibrary:
    def __init__(self, root):self.root=Path(root)
    def listing(self):
        from .studio_store import read_json
        return [read_json(p,{}) for p in sorted(safe_directory(self.root/'presets').glob('*.json')) if re.fullmatch(r'[0-9a-f]{32}\.json',p.name) and not p.is_symlink()]
    def preset_path(self,key):
        if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{32}',key):raise ValueError('ID bộ thương hiệu không hợp lệ')
        p=safe_directory(self.root/'presets')/(key+'.json')
        if p.is_symlink():raise ValueError('Đường dẫn không hợp lệ')
        return p
    def save(self,name,config,project_dir,key=None):
        from .studio_store import atomic_json
        if not isinstance(name,str) or not 1<=len(name.strip())<=80:raise ValueError('Tên bộ thương hiệu cần 1–80 ký tự')
        config=validate(config)
        for layer in config.values():layer.update(start=None,end=None)
        logo=config['logo']['image']
        if logo:
            with Image.open(asset_path(project_dir,logo)) as image:
                put_png(self.root/'logos',image.convert('RGBA'))
        key=key or uuid.uuid4().hex
        result=dict(id=key,name=name.strip(),branding=config)
        atomic_json(self.preset_path(key),result)
        return result
    def load(self,key,project_dir):
        from .studio_store import read_json
        p=self.preset_path(key)
        if not p.is_file():raise FileNotFoundError('Không tìm thấy bộ thương hiệu')
        result=read_json(p,{})
        logo=result['branding']['logo']['image']
        if logo:
            with Image.open(asset_path(self.root/'logos',logo)) as image:put_png(project_dir,image.convert('RGBA'))
        return result


def preview_frame(video, aspect, at):
    """Decode one local frame into memory; no render job or temporary media."""
    import cv2
    if aspect not in ('original','9:16','16:9','1:1') or not math.isfinite(at) or at < 0:
        raise ValueError('Mốc ảnh hoặc tỷ lệ không hợp lệ')
    cap = cv2.VideoCapture(str(video))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        if fps <= 0 or frames <= 0 or at > frames / fps:
            raise ValueError('Mốc lấy ảnh nằm ngoài video')
        cap.set(cv2.CAP_PROP_POS_MSEC, min(at, max(0, (frames-1)/fps))*1000)
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok:
        raise ValueError('Không lấy được ảnh tại mốc này. Hãy chọn mốc khác.')
    height, width = frame.shape[:2]
    w, h = dimensions(dict(width=width,height=height),aspect)
    left, top = (width-w)//2, (height-h)//2
    frame = frame[top:top+h,left:left+w]
    scale = min(1,1280/max(w,h))
    if scale < 1:
        frame = cv2.resize(frame,(max(1,round(w*scale)),max(1,round(h*scale))),interpolation=cv2.INTER_AREA)
    ok, content = cv2.imencode('.jpg',frame,[cv2.IMWRITE_JPEG_QUALITY,90])
    if not ok:
        raise ValueError('Không tạo được ảnh xem trước')
    return content.tobytes()
