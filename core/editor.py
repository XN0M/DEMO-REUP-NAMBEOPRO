import os
import re
import math
import shutil
import tempfile
import subprocess
from typing import Optional, Dict, Callable
import imageio_ffmpeg
from core.subtitler import DouyinSubtitler

class VideoEditor:
    """
    Studio bien tap va chuyen doi video chuyen nghiep:
    1. Cat cup moc thoi gian A-B (Trim).
    2. Chuyen doi ti le khung hinh (9:16 doc TikTok, 16:9 ngang YouTube, 1:1) kem lam mo hau canh (Smart Blur Background).
    3. Chong quet ban quyen: Lat guong (Flip ngang), Phong to 105% (Zoom), Chinh toc do (Speed).
    4. Lam mo phu de goc (Smart Bottom Blur) de che sub cu chu Han/Anh.
    5. Ep phu de tieng Viet va long tieng tron goi trong 1 lan render duy nhat (Single-pass pipeline).
    """

    def __init__(self):
        self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        self.tasks: Dict[str, Dict] = {}

    def get_video_duration(self, video_path: str) -> float:
        """Lay thoi luong video tinh theo giay."""
        try:
            cmd = [self.ffmpeg_exe, "-i", video_path]
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
        except Exception:
            pass
        return 0.0

    def render_final_video(
        self,
        input_video: str,
        output_video: str,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        aspect_ratio: str = "original",
        blur_background: bool = True,
        blur_bottom_sub: bool = False,
        flip_horizontal: bool = False,
        zoom_percent: float = 1.0,
        speed: float = 1.0,
        srt_path: Optional[str] = None,
        dubbed_audio_path: Optional[str] = None,
        font_size: int = 22,
        font_color: str = "white",
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> str:
        """
        Thuc thi toan bo quy trinh bien tap trong 1 lan chay FFmpeg duy nhat.
        """
        if not os.path.exists(input_video):
            raise FileNotFoundError(f"Khong tim thay video nguon: {input_video}")

        total_dur = self.get_video_duration(input_video)
        effective_start = max(0.0, float(start_time or 0.0))
        effective_end = min(total_dur, float(end_time)) if (end_time and float(end_time) > 0) else total_dur
        target_duration = max(0.5, effective_end - effective_start)
        if speed and speed > 0:
            target_duration = target_duration / speed

        temp_files_to_clean = []

        try:
            # 1. Xu ly phu de: Cat va dich chuyen timestamp neu can
            prepared_srt = None
            if srt_path and os.path.exists(srt_path):
                if effective_start > 0 or (end_time and effective_end < total_dur):
                    shifted_srt = DouyinSubtitler.shift_and_filter_subtitles(
                        srt_path,
                        start_sec=effective_start,
                        end_sec=effective_end
                    )
                    prepared_srt = shifted_srt
                    temp_files_to_clean.append(shifted_srt)
                else:
                    # Tao file tam tranh loi ky tu dac biet
                    tmp_srt = os.path.join(tempfile.gettempdir(), f"sub_{os.getpid()}_{int(effective_start)}.srt")
                    shutil.copy2(srt_path, tmp_srt)
                    prepared_srt = tmp_srt
                    temp_files_to_clean.append(tmp_srt)

            # 2. Xay dung video filtergraph
            v_filters = []

            # Toc do video
            if speed and abs(speed - 1.0) > 0.01:
                v_filters.append(f"setpts=PTS/{speed}")

            # Lat guong
            if flip_horizontal:
                v_filters.append("hflip")

            # Phong to (Zoom)
            if zoom_percent and zoom_percent > 1.01:
                z = round(zoom_percent, 3)
                v_filters.append(f"scale={z}*iw:{z}*ih,crop=iw:ih")

            # Lam mo phu de goc (18% day man hinh)
            # Tach layer, cat vung day lam mo, roi de len lai
            has_bottom_blur = blur_bottom_sub

            # Xu ly aspect ratio
            aspect_ratio = (aspect_ratio or "original").lower().strip()
            canvas_w, canvas_h = None, None
            if aspect_ratio == "9:16":
                canvas_w, canvas_h = 1080, 1920
            elif aspect_ratio == "16:9":
                canvas_w, canvas_h = 1920, 1080
            elif aspect_ratio == "1:1":
                canvas_w, canvas_h = 1080, 1080

            filter_complex_parts = []
            cur_tag = "0:v"

            # Buoc 1: Ap dung cac bo loc don (Speed, Flip, Zoom) len input video
            if v_filters:
                filter_complex_parts.append(f"[{cur_tag}]{','.join(v_filters)}[v_pre]")
                cur_tag = "v_pre"

            # Buoc 2: Lam mo sub day neu co
            if has_bottom_blur:
                filter_complex_parts.append(
                    f"[{cur_tag}]split=2[v_full][v_sub_area];"
                    f"[v_sub_area]crop=iw:ih*0.18:0:ih*0.82,boxblur=15:3[v_sub_blurred];"
                    f"[v_full][v_sub_blurred]overlay=0:main_h*0.82[v_blur_applied]"
                )
                cur_tag = "v_blur_applied"

            # Buoc 3: Chuyen doi canvas & Lam mo nen neu can
            if canvas_w and canvas_h:
                if blur_background:
                    filter_complex_parts.append(
                        f"[{cur_tag}]split=2[bg_in][fg_in];"
                        f"[bg_in]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,crop={canvas_w}:{canvas_h},boxblur=25:5[bg_layer];"
                        f"[fg_in]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease[fg_layer];"
                        f"[bg_layer][fg_layer]overlay=(W-w)/2:(H-h)/2[v_canvas]"
                    )
                    cur_tag = "v_canvas"
                else:
                    filter_complex_parts.append(
                        f"[{cur_tag}]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease,pad={canvas_w}:{canvas_h}:(ow-iw)/2:(oh-ih)/2:black[v_canvas]"
                    )
                    cur_tag = "v_canvas"

            # Buoc 4: Ep phu de tieng Viet
            if prepared_srt and os.path.exists(prepared_srt):
                safe_srt = prepared_srt.replace('\\', '/').replace(':', '\\:')
                style_str = DouyinSubtitler.build_force_style(
                    font_size=font_size,
                    font_color=font_color,
                    position="bottom",
                    mask_original=False
                )
                filter_complex_parts.append(f"[{cur_tag}]subtitles='{safe_srt}':force_style='{style_str}'[v_final]")
                cur_tag = "v_final"

            # 3. Chuan bi Audio
            # Neu co dubbed_audio_path (long tieng moi): map tu file audio nay
            # Neu khong co: map tu audio goc
            cmd = [self.ffmpeg_exe, "-y"]

            # Input 0: Video goc (kem ss va to)
            if effective_start > 0:
                cmd.extend(["-ss", str(effective_start)])
            if effective_end and effective_end < total_dur:
                cmd.extend(["-to", str(effective_end)])
            cmd.extend(["-i", input_video])

            audio_input_idx = 0
            if dubbed_audio_path and os.path.exists(dubbed_audio_path):
                # Input 1: Audio long tieng
                if effective_start > 0:
                    cmd.extend(["-ss", str(effective_start)])
                if effective_end and effective_end < total_dur:
                    cmd.extend(["-to", str(effective_end)])
                cmd.extend(["-i", dubbed_audio_path])
                audio_input_idx = 1

            # Audio speed
            a_filter = []
            if speed and abs(speed - 1.0) > 0.01:
                a_filter.append(f"atempo={speed}")

            if a_filter:
                filter_complex_parts.append(f"[{audio_input_idx}:a]{','.join(a_filter)}[a_final]")
                audio_out_tag = "[a_final]"
            else:
                audio_out_tag = f"{audio_input_idx}:a"

            if filter_complex_parts:
                filter_str = ";".join(filter_complex_parts)
                cmd.extend(["-filter_complex", filter_str])
                cmd.extend(["-map", f"[{cur_tag}]"])
            else:
                cmd.extend(["-map", "0:v"])

            cmd.extend(["-map", audio_out_tag if audio_out_tag.startswith("[") else f"{audio_input_idx}:a?"])

            # Video & Audio encoding settings
            cmd.extend([
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "21",
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                output_video
            ])

            if progress_callback:
                progress_callback(10.0, "Đang khởi tạo tiến trình render...")

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore"
            )

            time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
            buf = ""
            while True:
                chunk = process.stderr.read(256)
                if not chunk and process.poll() is not None:
                    break
                buf += chunk
                matches = list(time_pattern.finditer(buf))
                if matches and target_duration > 0:
                    curr_sec = float(matches[-1].group(1)) * 3600 + float(matches[-1].group(2)) * 60 + float(matches[-1].group(3))
                    pct = min(98.0, 10.0 + (curr_sec / target_duration) * 88.0)
                    if progress_callback:
                        progress_callback(round(pct, 1), f"Đang dựng & xuất video ({int(pct)}%)...")
                    buf = buf[-500:]

            process.wait()
            if process.returncode != 0:
                raise RuntimeError(f"Lỗi FFmpeg khi render video: {output_video}")

            if progress_callback:
                progress_callback(100.0, "Đã xuất video hoàn chỉnh thành công!")

            return output_video

        finally:
            for f in temp_files_to_clean:
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # CROP + TRIM TOOL (Tab 5 - Chỉnh sửa Video)
    # ------------------------------------------------------------------

    def crop_trim_video(
        self,
        input_video: str,
        output_video: str,
        crop: Optional[Dict] = None,
        trim: Optional[Dict] = None,
        lossless_trim: bool = True,
        progress_callback: Optional[Callable[[float, str], None]] = None
    ) -> str:
        """
        Cắt vùng khung hình (crop) và/hoặc cắt đoạn thời gian (trim).

        crop: {"x": int, "y": int, "w": int, "h": int} — pixel tuyệt đối
        trim: {"start": float, "end": float} — giây
        lossless_trim: True = -c copy (nhanh, có thể lệch vài frame);
                       False = re-encode CRF23 (chậm hơn, chính xác)
        """
        if not os.path.exists(input_video):
            raise FileNotFoundError(f"Không tìm thấy video: {input_video}")

        has_crop = (crop and crop.get("w", 0) > 0 and crop.get("h", 0) > 0)
        has_trim = (trim and (trim.get("start", 0) > 0 or trim.get("end", 0) > 0))

        if not has_crop and not has_trim:
            raise ValueError("Phải chọn ít nhất Crop hoặc Trim")

        # Tính thời lượng để ước tính tiến trình
        total_dur = self.get_video_duration(input_video)
        if has_trim:
            t_start = float(trim.get("start", 0) or 0)
            t_end = float(trim.get("end", 0) or total_dur)
            t_end = min(t_end, total_dur)
            target_dur = max(0.5, t_end - t_start)
        else:
            t_start = 0.0
            t_end = total_dur
            target_dur = total_dur

        if progress_callback:
            progress_callback(5.0, "Đang khởi động FFmpeg...")

        # Build FFmpeg command
        cmd = [self.ffmpeg_exe, "-y"]

        # Trim bằng input seeking (trước -i) — nhanh và chính xác hơn output seeking
        if has_trim:
            cmd += ["-ss", str(t_start)]
            if t_end < total_dur:
                cmd += ["-to", str(t_end)]

        cmd += ["-i", input_video]

        if has_crop:
            crop_filter = "crop={w}:{h}:{x}:{y}".format(**crop)
            if lossless_trim and not has_crop:
                # Trim only lossless — copy codec
                cmd += ["-c", "copy"]
            else:
                # Crop requires re-encode (can't copy with vf filter)
                cmd += [
                    "-vf", crop_filter,
                    "-c:v", "libx264",
                    "-crf", "23",
                    "-preset", "fast",
                    "-c:a", "aac",
                    "-b:a", "192k",
                ]
        elif has_trim and lossless_trim:
            # Trim only, lossless
            cmd += ["-c", "copy"]
        else:
            # Trim re-encode (accurate)
            cmd += [
                "-c:v", "libx264",
                "-crf", "23",
                "-preset", "fast",
                "-c:a", "aac",
                "-b:a", "192k",
            ]

        cmd.append(output_video)

        # Run FFmpeg with progress parsing
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )

        time_pattern = re.compile(r"time=(\d+):(\d+):(\d+\.\d+)")
        buf = ""
        while True:
            chunk = process.stderr.read(256)
            if not chunk and process.poll() is not None:
                break
            buf += chunk
            matches = list(time_pattern.finditer(buf))
            if matches and target_dur > 0 and progress_callback:
                h, m, s = float(matches[-1].group(1)), float(matches[-1].group(2)), float(matches[-1].group(3))
                curr_sec = h * 3600 + m * 60 + s
                pct = min(95.0, 10.0 + (curr_sec / target_dur) * 85.0)
                progress_callback(round(pct, 1), f"Đang xử lý ({int(pct)}%)...")
                buf = buf[-500:]

        process.wait()
        if process.returncode != 0:
            stderr_tail = buf[-800:] if buf else "(no stderr)"
            raise RuntimeError(f"FFmpeg lỗi (code {process.returncode}): {stderr_tail}")

        if progress_callback:
            progress_callback(100.0, "Hoàn tất xử lý video!")

        return output_video

    async def execute_crop_trim(
        self,
        task_id: str,
        input_video: str,
        output_video: str,
        crop: Optional[Dict] = None,
        trim: Optional[Dict] = None,
        lossless_trim: bool = True
    ) -> None:
        """Chạy crop_trim_video trong executor (không block event loop)."""
        import asyncio

        task_data = self.tasks.setdefault(task_id, {
            "task_id": task_id,
            "status": "processing",
            "percent": 0.0,
            "progress_text": "Đang chuẩn bị...",
            "output_video": None,
            "error": None
        })

        def _progress(pct: float, msg: str):
            task_data["percent"] = pct
            task_data["progress_text"] = msg

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                lambda: self.crop_trim_video(
                    input_video=input_video,
                    output_video=output_video,
                    crop=crop,
                    trim=trim,
                    lossless_trim=lossless_trim,
                    progress_callback=_progress
                )
            )
            task_data["status"] = "completed"
            task_data["percent"] = 100.0
            task_data["output_video"] = output_video
        except Exception as e:
            task_data["status"] = "error"
            task_data["error"] = str(e)
            task_data["progress_text"] = f"Lỗi: {e}"

