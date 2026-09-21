import os
import re
import uuid
import shutil
import asyncio
import subprocess
import threading
from typing import Dict, Optional, Callable
import imageio_ffmpeg

class DouyinSubtitler:
    def __init__(self):
        self.tasks: Dict[str, Dict] = {}

    def get_ffmpeg_exe(self) -> str:
        """Get path to bundled FFmpeg binary."""
        try:
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            if exe and os.path.exists(exe):
                return exe
        except Exception:
            pass
        return "ffmpeg"

    def get_media_duration(self, file_path: str) -> float:
        """Get media duration in seconds via FFmpeg probe."""
        ffmpeg_exe = self.get_ffmpeg_exe()
        cmd = [ffmpeg_exe, "-i", file_path]
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", p.stderr)
        if match:
            h = float(match.group(1))
            m = float(match.group(2))
            s = float(match.group(3))
            return h * 3600 + m * 60 + s
        return 0.0

    def get_video_bitrate(self, file_path: str) -> int:
        """Get video bitrate in kbps via FFmpeg probe."""
        ffmpeg_exe = self.get_ffmpeg_exe()
        try:
            cmd = [ffmpeg_exe, "-i", file_path]
            p = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore"
            )
            match = re.search(r"bitrate:\s*(\d+)\s*kb/s", p.stderr)
            if match:
                return int(match.group(1))
        except Exception:
            pass
        return 0

    @staticmethod
    def build_force_style(
        font_size: int = 22,
        font_color: str = "white",
        position: str = "overlay_original",
        mask_original: bool = True
    ) -> str:
        """
        Build ASS force_style string for FFmpeg subtitles filter.
        ASS color format is &HAABBGGRR&.
        """
        colors = {
            "white": "&H00FFFFFF&",     # White
            "gold": "&H0000D7FF&",      # Warm gold / Vàng cam ấm
            "yellow": "&H0000FFFF&",    # Lemon yellow / Vàng chanh
            "cyan": "&H00FFFF00&",      # Cyan / Xanh ngọc
            "green": "&H0000FF00&"      # Bright green
        }
        primary_col = colors.get(str(font_color).lower(), "&H00FFFFFF&")

        positions = {
            "overlay_original": (2, 55), # Bottom, raised to cover original Douyin sub (MarginV 55px)
            "bottom": (2, 25),          # Close to bottom (MarginV 25px)
            "above_tiktok": (2, 95),    # Higher up to avoid TikTok captions/controls (MarginV 95px)
            "center": (5, 0)            # Center screen
        }
        align, margin_v = positions.get(str(position).lower(), (2, 55))

        if mask_original:
            # BorderStyle=3 draws an opaque/translucent bounding box around the text.
            # BackColour=&H4C000000& gives ~70% opacity black box (Netflix/CapCut style).
            # Outline specifies padding around the box (5px).
            return (
                f"Fontname=Arial,Fontsize={font_size},PrimaryColour={primary_col},"
                f"OutlineColour=&H00000000&,BackColour=&H4C000000&,"
                f"BorderStyle=3,Outline=5,Shadow=0,Alignment={align},MarginV={margin_v}"
            )
        else:
            # Traditional text with crisp black outline and soft drop shadow
            return (
                f"Fontname=Arial,Fontsize={font_size},PrimaryColour={primary_col},"
                f"OutlineColour=&H00000000&,BackColour=&H80000000&,"
                f"BorderStyle=1,Outline=2.0,Shadow=1.0,Alignment={align},MarginV={margin_v}"
            )

    def start_subtitle_task(
        self,
        video_path: str,
        srt_path: str,
        output_path: str,
        mode: str = "hardsub",
        language: str = "vi",
        font_size: int = 22,
        font_color: str = "white",
        position: str = "overlay_original",
        mask_original: bool = True
    ) -> str:
        """Start background subtitle rendering task and return task_id."""
        task_id = str(uuid.uuid4())
        self.tasks[task_id] = {
            "task_id": task_id,
            "status": "processing",
            "progress": 5,
            "message": "Đang chuẩn bị tạo video phụ đề...",
            "mode": mode,
            "language": language,
            "output_video": None,
            "error": None
        }

        def _worker():
            try:
                self.burn_subtitles_sync(
                    video_path=video_path,
                    srt_path=srt_path,
                    output_path=output_path,
                    mode=mode,
                    language=language,
                    task_id=task_id,
                    font_size=font_size,
                    font_color=font_color,
                    position=position,
                    mask_original=mask_original
                )
            except Exception as e:
                if task_id in self.tasks:
                    self.tasks[task_id]["status"] = "error"
                    self.tasks[task_id]["error"] = str(e)
                    self.tasks[task_id]["message"] = f"Lỗi: {str(e)}"

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return task_id

    def burn_subtitles_sync(
        self,
        video_path: str,
        srt_path: str,
        output_path: str,
        mode: str = "hardsub",
        language: str = "vi",
        task_id: Optional[str] = None,
        font_size: int = 22,
        font_color: str = "white",
        position: str = "overlay_original",
        mask_original: bool = True
    ) -> Dict:
        """
        Burn subtitles (hardsub) or embed subtitle stream (softsub) into MP4.
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"Không tìm thấy video: {video_path}")
        if not os.path.exists(srt_path):
            raise FileNotFoundError(f"Không tìm thấy file phụ đề: {srt_path}")

        ffmpeg_exe = self.get_ffmpeg_exe()
        total_duration = self.get_media_duration(video_path)
        if total_duration <= 0:
            total_duration = 30.0

        if task_id and task_id in self.tasks:
            self.tasks[task_id]["status"] = "processing"
            self.tasks[task_id]["progress"] = 10
            self.tasks[task_id]["message"] = "Đang khởi tạo FFmpeg..."

        # Priority flag on Windows to prevent system lag
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)

        # Softsub mode: Instant mux without video re-encode (0.5s)
        if mode == "softsub":
            if task_id and task_id in self.tasks:
                self.tasks[task_id]["progress"] = 50
                self.tasks[task_id]["message"] = "Đang nhúng luồng phụ đề vào file MP4 (Softsub)..."

            cmd = [
                ffmpeg_exe, "-y",
                "-i", video_path,
                "-i", srt_path,
                "-c:v", "copy",
                "-c:a", "copy",
                "-c:s", "mov_text",
                "-metadata:s:s:0", f"language={'vie' if language == 'vi' else 'chi'}",
                "-movflags", "+faststart",
                output_path
            ]
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
                text=True,
                errors="ignore"
            )
            if res.returncode != 0:
                raise RuntimeError(f"Lỗi FFmpeg khi tạo Softsub: {res.stderr[-400:]}")

            if task_id and task_id in self.tasks:
                self.tasks[task_id]["status"] = "completed"
                self.tasks[task_id]["progress"] = 100
                self.tasks[task_id]["message"] = "Tạo video Softsub thành công!"
                self.tasks[task_id]["output_video"] = output_path

            return {"status": "success", "output_video": output_path, "mode": "softsub"}

        # Hardsub mode: Render subtitles directly onto video frames
        # Use a safe ASCII temporary SRT filename in the same directory to avoid FFmpeg filter parsing errors
        video_dir = os.path.dirname(os.path.abspath(video_path))
        temp_token = uuid.uuid4().hex[:8]
        temp_srt_name = f"_sub_tmp_{temp_token}.srt"
        temp_srt_path = os.path.join(video_dir, temp_srt_name)

        try:
            shutil.copy2(srt_path, temp_srt_path)

            if task_id and task_id in self.tasks:
                self.tasks[task_id]["progress"] = 15
                self.tasks[task_id]["message"] = "Đang vẽ phụ đề chữ nổi lên khung hình video (Hardsub)..."

            force_style = self.build_force_style(
                font_size=font_size,
                font_color=font_color,
                position=position,
                mask_original=mask_original
            )

            # Smart bitrate clamping: match original video size and quality without bloating
            orig_bitrate = self.get_video_bitrate(video_path)
            v_args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
            if orig_bitrate > 400:
                max_rate_k = int(orig_bitrate * 1.05)
                buf_size_k = int(orig_bitrate * 2.0)
                v_args.extend(["-maxrate", f"{max_rate_k}k", "-bufsize", f"{buf_size_k}k"])
            else:
                v_args.extend(["-maxrate", "4000k", "-bufsize", "8000k"])

            cmd = [
                ffmpeg_exe, "-y",
                "-i", os.path.basename(video_path),
                "-vf", f"subtitles={temp_srt_name}:force_style='{force_style}'",
                *v_args,
                "-c:a", "copy",
                "-threads", "4",
                "-movflags", "+faststart",
                os.path.abspath(output_path)
            ]

            process = subprocess.Popen(
                cmd,
                cwd=video_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=creationflags,
                text=True,
                encoding="utf-8",
                errors="ignore"
            )

            # Read progress from stderr (handles both \r and \n)
            time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
            buf = ""
            while True:
                chunk = process.stderr.read(256)
                if not chunk and process.poll() is not None:
                    break
                buf += chunk
                matches = list(time_pattern.finditer(buf))
                if matches:
                    curr_sec = float(matches[-1].group(1)) * 3600 + float(matches[-1].group(2)) * 60 + float(matches[-1].group(3))
                    pct = min(98.0, 15.0 + (curr_sec / total_duration) * 83.0)
                    if task_id and task_id in self.tasks:
                        self.tasks[task_id]["progress"] = round(pct, 1)
                        self.tasks[task_id]["message"] = f"Đang chèn phụ đề ({int(pct)}%)..."
                    buf = buf[-500:]

            process.wait()
            if process.returncode != 0:
                raise RuntimeError("Lỗi FFmpeg khi chèn phụ đề Hardsub.")

            if task_id and task_id in self.tasks:
                self.tasks[task_id]["status"] = "completed"
                self.tasks[task_id]["progress"] = 100
                self.tasks[task_id]["message"] = "Đã xuất video có phụ đề thành công!"
                self.tasks[task_id]["output_video"] = output_path

            return {"status": "success", "output_video": output_path, "mode": "hardsub"}

        finally:
            if os.path.exists(temp_srt_path):
                try:
                    os.remove(temp_srt_path)
                except Exception:
                    pass

    @staticmethod
    def parse_srt_time(time_str: str) -> float:
        """Chuyen timestamp SRT 00:01:23,456 thanh so giay (float)."""
        time_str = time_str.strip().replace(',', '.')
        parts = time_str.split(':')
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        elif len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return float(time_str)

    @staticmethod
    def format_srt_time(seconds: float) -> str:
        """Chuyen so giay thanh timestamp chuan SRT 00:01:23,456."""
        if seconds < 0:
            seconds = 0
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds - int(seconds)) * 1000))
        if ms >= 1000:
            ms = 999
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @classmethod
    def shift_and_filter_subtitles(
        cls,
        srt_path: str,
        start_sec: float = 0.0,
        end_sec: Optional[float] = None,
        output_srt_path: Optional[str] = None
    ) -> str:
        """
        Cat va dich chuyen moc thoi gian phu de khi cat video:
        - Giu lai cac cau thoai co giao voi khoang [start_sec, end_sec].
        - Tru di start_sec de phu de dong bo chuan xac voi video da cat.
        """
        if not os.path.exists(srt_path):
            raise FileNotFoundError(f"Khong tim thay file phụ đề: {srt_path}")

        if not output_srt_path:
            base, ext = os.path.splitext(srt_path)
            output_srt_path = f"{base}.trimmed{ext}"

        with open(srt_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        # Regex match tung block SRT
        pattern = re.compile(r"(\d+)\r?\n(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\r?\n(.*?)(?=\r?\n\r?\n|\Z)", re.DOTALL)
        blocks = list(pattern.finditer(content))

        new_cues = []
        cue_idx = 1
        for m in blocks:
            c_start = cls.parse_srt_time(m.group(2))
            c_end = cls.parse_srt_time(m.group(3))
            text = m.group(4).strip()

            # Kiem tra xem cau co giao voi khoang [start_sec, end_sec] khong
            if end_sec is not None and c_start >= end_sec:
                continue
            if c_end <= start_sec:
                continue

            # Crop thoi gian neu can
            adj_start = max(0.0, c_start - start_sec)
            adj_end = max(adj_start + 0.3, (min(c_end, end_sec) if end_sec else c_end) - start_sec)

            start_str = cls.format_srt_time(adj_start)
            end_str = cls.format_srt_time(adj_end)

            new_cues.append(f"{cue_idx}\n{start_str} --> {end_str}\n{text}")
            cue_idx += 1

        with open(output_srt_path, "w", encoding="utf-8") as f:
            f.write("\n\n".join(new_cues) + "\n")

        return output_srt_path

