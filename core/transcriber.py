import asyncio
import json
import os
import time
from typing import Callable, Dict, List, Optional
from faster_whisper import WhisperModel

class DouyinTranscriber:
    def __init__(self):
        self.models: Dict[str, WhisperModel] = {}
        self.tasks: Dict[str, Dict] = {}

    def get_model(self, model_size: str = "base") -> WhisperModel:
        """Load and cache Whisper model with int8 quantization for ultra-fast CPU inference."""
        if model_size not in self.models:
            print(f"[*] Đang tải mô hình Whisper '{model_size}' (CPU int8)...")
            self.models[model_size] = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8"
            )
            print(f"[+] Mô hình Whisper '{model_size}' đã sẵn sàng.")
        return self.models[model_size]

    @staticmethod
    def format_srt_time(seconds: float) -> str:
        """Convert float seconds to SRT time format: HH:MM:SS,mmm"""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int(round((seconds - int(seconds)) * 1000))
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @staticmethod
    def generate_srt(segments: List[Dict], lang_field: str = "text") -> str:
        """Generate standard SRT subtitle string."""
        lines = []
        for i, seg in enumerate(segments, start=1):
            start_str = DouyinTranscriber.format_srt_time(seg["start"])
            end_str = DouyinTranscriber.format_srt_time(seg["end"])
            text = seg.get(lang_field) or seg.get("text", "")
            lines.append(f"{i}\n{start_str} --> {end_str}\n{text}\n")
        return "\n".join(lines)

    @staticmethod
    def generate_txt(segments: List[Dict], include_timestamps: bool = True) -> str:
        """Generate plain text script."""
        lines = []
        for seg in segments:
            text = seg.get("vietnamese") or seg.get("text", "")
            orig = seg.get("chinese") or seg.get("text", "")
            if include_timestamps:
                start_str = DouyinTranscriber.format_srt_time(seg["start"])[:8]
                end_str = DouyinTranscriber.format_srt_time(seg["end"])[:8]
                if "vietnamese" in seg and seg["vietnamese"] and seg["vietnamese"] != orig:
                    lines.append(f"[{start_str} - {end_str}] {orig}\n    -> {text}\n")
                else:
                    lines.append(f"[{start_str} - {end_str}] {orig or text}\n")
            else:
                if "vietnamese" in seg and seg["vietnamese"] and seg["vietnamese"] != orig:
                    lines.append(f"{orig} ({text})")
                else:
                    lines.append(orig or text)
        return "\n".join(lines)

    async def transcribe_video(
        self,
        video_path: str,
        task_id: str,
        model_size: str = "base",
        language: str = "zh",
        on_progress: Optional[Callable[[Dict], None]] = None
    ) -> Dict:
        """Asynchronously transcribe video file using ThreadPoolExecutor."""
        task_data = {
            "task_id": task_id,
            "video_path": video_path,
            "filename": os.path.basename(video_path),
            "model_size": model_size,
            "status": "processing",
            "progress_text": f"Đang khởi động AI Whisper ({model_size})...",
            "percent": 5.0,
            "segments": [],
            "character_map": None,
            "error": None
        }
        self.tasks[task_id] = task_data
        if on_progress:
            on_progress(task_data)

        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None,
                self._sync_transcribe,
                video_path,
                model_size,
                language,
                task_data,
                on_progress
            )
            return result
        except Exception as e:
            task_data["status"] = "failed"
            task_data["error"] = str(e)
            task_data["progress_text"] = f"Lỗi: {str(e)}"
            if on_progress:
                on_progress(task_data)
            return task_data

    def _sync_transcribe(
        self,
        video_path: str,
        model_size: str,
        language: str,
        task_data: Dict,
        on_progress: Optional[Callable[[Dict], None]]
    ) -> Dict:
        task_data["progress_text"] = f"Nạp mô hình AI Whisper ({model_size})..."
        task_data["percent"] = 15.0
        if on_progress:
            on_progress(task_data)

        model = self.get_model(model_size)

        task_data["progress_text"] = "Đang nhận diện giọng nói & tách câu thoại..."
        task_data["percent"] = 30.0
        if on_progress:
            on_progress(task_data)

        # PyAV inside faster-whisper decodes video/audio streams automatically
        segments_generator, info = model.transcribe(
            video_path,
            beam_size=5,
            language=language if (language and language != "auto") else None,
            vad_filter=True, # Voice Activity Detection (filters music / silence)
            vad_parameters=dict(min_silence_duration_ms=400)
        )

        detected_lang = getattr(info, "language", language) or "auto"
        task_data["detected_language"] = detected_lang
        is_vietnamese = (detected_lang == "vi")

        total_duration = info.duration if info.duration and info.duration > 0 else 1.0
        segments = []

        for seg in segments_generator:
            cur_sec = seg.end
            pct = min(95.0, 30.0 + (cur_sec / total_duration) * 65.0)
            task_data["percent"] = round(pct, 1)
            task_data["progress_text"] = f"Đã nhận diện đến giây {int(cur_sec)}/{int(total_duration)}s ({len(segments) + 1} câu thoại)..."

            seg_item = {
                "id": len(segments) + 1,
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
                "chinese": "" if is_vietnamese else seg.text.strip(),
                "vietnamese": seg.text.strip() if is_vietnamese else ""
            }
            segments.append(seg_item)
            task_data["segments"] = segments
            if on_progress:
                on_progress(task_data)

        task_data["segments"] = segments
        task_data["status"] = "completed"
        task_data["percent"] = 100.0
        task_data["progress_text"] = f"Hoàn tất! Đã trích xuất {len(segments)} câu thoại (Ngôn ngữ: {detected_lang.upper()})."

        # Save to disk
        base_name = os.path.splitext(video_path)[0]
        json_save_path = f"{base_name}.transcript.json"
        srt_save_path = f"{base_name}.srt"

        try:
            with open(json_save_path, "w", encoding="utf-8") as f:
                json.dump({
                    "video_path": video_path,
                    "model_size": model_size,
                    "detected_language": detected_lang,
                    "total_segments": len(segments),
                    "duration": total_duration,
                    "segments": segments,
                    "character_map": None
                }, f, ensure_ascii=False, indent=2)

            srt_content = self.generate_srt(segments, "vietnamese" if is_vietnamese else "chinese")
            with open(srt_save_path, "w", encoding="utf-8") as f:
                f.write(srt_content)

            if is_vietnamese:
                vi_srt_path = f"{base_name}.vi.srt"
                with open(vi_srt_path, "w", encoding="utf-8") as f:
                    f.write(self.generate_srt(segments, "vietnamese"))

                txt_path = f"{base_name}.transcript.txt"
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(self.generate_txt(segments, include_timestamps=True))
        except Exception as e:
            print(f"Lỗi khi lưu file transcript: {e}")

        if on_progress:
            on_progress(task_data)

        return task_data
