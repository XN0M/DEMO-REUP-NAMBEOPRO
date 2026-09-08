import os
import re
import math
import wave
import struct
import shutil
import asyncio
import tempfile
import subprocess
from typing import List, Dict, Optional, Tuple, Callable
import httpx
import imageio_ffmpeg

try:
    import audioop
except ImportError:
    import audioop_lts as audioop
    import sys
    sys.modules['audioop'] = audioop

import edge_tts

VOICE_CATALOG = [
    {
        "id": "vi-VN-HoaiMyNeural",
        "name": "Hoài My (Nữ - Tự nhiên, Biểu cảm)",
        "gender": "female",
        "lang": "vi-VN",
        "provider": "edge",
        "free": True
    },
    {
        "id": "vi-VN-NamMinhNeural",
        "name": "Nam Minh (Nam - Chuẩn giọng, Trầm ấm)",
        "gender": "male",
        "lang": "vi-VN",
        "provider": "edge",
        "free": True
    },
    {
        "id": "alloy",
        "name": "Alloy (Trung tính - OpenAI)",
        "gender": "neutral",
        "lang": "multilingual",
        "provider": "openai",
        "free": False
    },
    {
        "id": "echo",
        "name": "Echo (Nam - OpenAI)",
        "gender": "male",
        "lang": "multilingual",
        "provider": "openai",
        "free": False
    },
    {
        "id": "fable",
        "name": "Fable (Kể chuyện - OpenAI)",
        "gender": "neutral",
        "lang": "multilingual",
        "provider": "openai",
        "free": False
    },
    {
        "id": "onyx",
        "name": "Onyx (Nam trầm - OpenAI)",
        "gender": "male",
        "lang": "multilingual",
        "provider": "openai",
        "free": False
    },
    {
        "id": "nova",
        "name": "Nova (Nữ năng động - OpenAI)",
        "gender": "female",
        "lang": "multilingual",
        "provider": "openai",
        "free": False
    },
    {
        "id": "shimmer",
        "name": "Shimmer (Nữ trong trẻo - OpenAI)",
        "gender": "female",
        "lang": "multilingual",
        "provider": "openai",
        "free": False
    }
]

class DouyinDubber:
    def __init__(self):
        self.tasks: Dict[str, Dict] = {}
        self.sample_rate = 44100
        self.channels = 2
        self.sampwidth = 2 # 16-bit
        self.frame_size = self.channels * self.sampwidth

    def get_ffmpeg_exe(self) -> str:
        """Get path to FFmpeg binary."""
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

    def check_video_has_audio(self, video_path: str) -> bool:
        """Check if video file contains an audio stream."""
        ffmpeg_exe = self.get_ffmpeg_exe()
        cmd = [ffmpeg_exe, "-i", video_path]
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore"
        )
        return "Audio:" in p.stderr

    def get_available_voices(self) -> List[Dict]:
        """Return list of supported TTS voices."""
        return VOICE_CATALOG

    async def synthesize_edge(self, text: str, voice: str = "vi-VN-HoaiMyNeural", retries: int = 3) -> bytes:
        """Synthesize text using Microsoft Edge TTS with automatic retries."""
        for attempt in range(retries):
            try:
                comm = edge_tts.Communicate(text, voice)
                audio_bytes = bytearray()
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        audio_bytes.extend(chunk["data"])
                if audio_bytes:
                    return bytes(audio_bytes)
            except Exception as e:
                if attempt == retries - 1:
                    raise RuntimeError(f"Edge TTS ({voice}) thất bại sau {retries} lần thử: {e}")
                await asyncio.sleep(0.5 * (attempt + 1))
        raise RuntimeError(f"Edge TTS không trả về dữ liệu âm thanh cho giọng {voice}.")

    async def synthesize_openai(
        self,
        text: str,
        voice: str = "alloy",
        api_key: str = "",
        base_url: str = "https://api.openai.com/v1"
    ) -> bytes:
        """Synthesize text using OpenAI TTS."""
        if not api_key:
            raise ValueError("Vui lòng cấu hình OpenAI API Key để sử dụng giọng đọc OpenAI.")

        clean_base = (base_url or "https://api.openai.com/v1").strip().rstrip("/")
        if not clean_base.endswith("/audio/speech"):
            endpoint = f"{clean_base}/audio/speech"
        else:
            endpoint = clean_base

        headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "tts-1",
            "input": text,
            "voice": voice,
            "response_format": "mp3"
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(endpoint, headers=headers, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"OpenAI TTS Error ({resp.status_code}): {resp.text}")
            return resp.content

    async def synthesize_sentence(
        self,
        text: str,
        voice: str = "vi-VN-HoaiMyNeural",
        provider: str = "edge",
        api_key: str = "",
        base_url: str = ""
    ) -> bytes:
        """Synthesize a single sentence with auto provider detection."""
        text = text.strip()
        if not text:
            return b""

        # Voice prefix / provider override
        if voice.startswith("vi-VN-") or provider == "edge":
            return await self.synthesize_edge(text, voice)
        else:
            return await self.synthesize_openai(text, voice, api_key, base_url)

    def _convert_mp3_to_wav(self, mp3_path: str, wav_path: str):
        """Convert MP3 to standardized 44.1kHz 16-bit stereo PCM WAV."""
        ffmpeg_exe = self.get_ffmpeg_exe()
        cmd = [
            ffmpeg_exe, "-y",
            "-i", mp3_path,
            "-ar", str(self.sample_rate),
            "-ac", str(self.channels),
            "-sample_fmt", "s16",
            "-f", "wav",
            wav_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise RuntimeError(f"Lỗi chuyển đổi MP3 sang WAV: {res.stderr.decode('utf-8', errors='ignore')}")

    def _stretch_wav(self, input_wav: str, output_wav: str, speed_factor: float):
        """Speed up or slow down WAV file using FFmpeg atempo filter."""
        ffmpeg_exe = self.get_ffmpeg_exe()
        # atempo accepts 0.5 to 2.0. If outside, chain filters.
        filters = []
        cur_speed = speed_factor
        while cur_speed > 2.0:
            filters.append("atempo=2.0")
            cur_speed /= 2.0
        while cur_speed < 0.5:
            filters.append("atempo=0.5")
            cur_speed /= 0.5
        filters.append(f"atempo={cur_speed:.4f}")
        filter_str = ",".join(filters)

        cmd = [
            ffmpeg_exe, "-y",
            "-i", input_wav,
            "-filter:a", filter_str,
            "-ar", str(self.sample_rate),
            "-ac", str(self.channels),
            "-sample_fmt", "s16",
            "-f", "wav",
            output_wav
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            raise RuntimeError(f"Lỗi thay đổi tốc độ âm thanh: {res.stderr.decode('utf-8', errors='ignore')}")

    def _get_wav_duration_and_frames(self, wav_path: str) -> Tuple[float, bytes]:
        """Read 44.1kHz stereo WAV frames and duration."""
        with wave.open(wav_path, "rb") as wf:
            frames_count = wf.getnframes()
            rate = wf.getframerate()
            duration = frames_count / float(rate)
            raw_frames = wf.readframes(frames_count)
            return duration, raw_frames

    async def execute_dubbing(
        self,
        video_path: str,
        transcript_data: dict,
        mode: str = "single",
        single_voice: str = "vi-VN-HoaiMyNeural",
        character_voices: Optional[Dict[str, str]] = None,
        bg_volume: float = 0.15,
        voice_volume: float = 1.2,
        openai_api_key: str = "",
        openai_base_url: str = "",
        task_id: str = ""
    ) -> Dict:
        """
        Execute full dubbing workflow:
        1. Parse segments & map voices
        2. Synthesize audio per segment
        3. Match timestamp slots & stretch if necessary
        4. Compose master audio track
        5. FFmpeg audio ducking mux with video
        """
        character_voices = character_voices or {}
        self.tasks[task_id] = {
            "status": "processing",
            "progress": 5,
            "message": "Bắt đầu chuẩn bị phụ đề và giọng đọc...",
            "error": None,
            "dubbed_video": None,
            "dubbed_audio": None
        }

        temp_dir = tempfile.mkdtemp(prefix="dub_")
        try:
            # 1. Prepare segments
            all_segments = transcript_data.get("segments", [])
            valid_segments = []
            for s in all_segments:
                txt = s.get("vietnamese") or s.get("chinese") or s.get("text", "")
                txt = txt.strip()
                if txt:
                    valid_segments.append({**s, "dub_text": txt})

            if not valid_segments:
                raise ValueError("Không có đoạn thoại nào để lồng tiếng (vui lòng dịch hoặc nhập phụ đề trước).")

            # 2. Get total video duration
            video_dur = self.get_media_duration(video_path)
            if video_dur <= 0:
                video_dur = transcript_data.get("duration", 0)
            max_seg_end = max(s["end"] for s in valid_segments)
            total_duration = max(video_dur, max_seg_end + 1.0)

            total_frames = int(math.ceil(total_duration * self.sample_rate))
            master_buffer = bytearray(total_frames * self.frame_size)

            total_segs = len(valid_segments)
            self.tasks[task_id]["message"] = f"Đang tạo giọng đọc cho {total_segs} câu thoại..."
            self.tasks[task_id]["progress"] = 10

            # Character map lookup if in multi mode
            char_map = transcript_data.get("character_map", {})
            characters_list = char_map.get("characters", [])
            char_gender_hint = {}
            for c in characters_list:
                c_name = c.get("name", "").lower()
                c_role = c.get("role", "").lower()
                c_pronoun = c.get("self_pronoun", "").lower()
                # Guess gender
                is_male = any(k in c_role or k in c_name or k in c_pronoun for k in ["nam", "ông", "bác", "anh", "chú", "đại thúc", "cậu", "cha", "bố"])
                is_female = any(k in c_role or k in c_name or k in c_pronoun for k in ["nữ", "bà", "cô", "chị", "dì", "mẹ", "em", "gái", "tiểu thư", "nương"])
                if is_male and not is_female:
                    char_gender_hint[c.get("name")] = "male"
                elif is_female:
                    char_gender_hint[c.get("name")] = "female"

            # 3. Process each segment
            for idx, seg in enumerate(valid_segments):
                text = seg["dub_text"]
                start_sec = float(seg["start"])
                end_sec = float(seg["end"])
                slot_duration = max(0.2, end_sec - start_sec)

                # Determine voice
                if mode == "multi":
                    speaker = seg.get("speaker") or seg.get("character") or ""
                    voice = character_voices.get(speaker)
                    if not voice:
                        # Infer voice from gender hint or single_voice
                        gender = char_gender_hint.get(speaker)
                        if gender == "male":
                            voice = "vi-VN-NamMinhNeural"
                        elif gender == "female":
                            voice = "vi-VN-HoaiMyNeural"
                        else:
                            voice = single_voice
                else:
                    voice = single_voice

                # Synthesize
                provider = "openai" if voice in ["alloy", "echo", "fable", "onyx", "nova", "shimmer"] else "edge"
                seg_mp3 = os.path.join(temp_dir, f"seg_{idx}.mp3")
                seg_wav = os.path.join(temp_dir, f"seg_{idx}.wav")

                audio_data = await self.synthesize_sentence(
                    text=text,
                    voice=voice,
                    provider=provider,
                    api_key=openai_api_key,
                    base_url=openai_base_url
                )

                with open(seg_mp3, "wb") as f:
                    f.write(audio_data)

                # Convert to standard WAV
                self._convert_mp3_to_wav(seg_mp3, seg_wav)

                # Duration check & stretch
                dur, frames = self._get_wav_duration_and_frames(seg_wav)
                if dur > slot_duration and slot_duration >= 0.4:
                    speed = dur / slot_duration
                    speed = min(1.65, speed) # Cap speedup at 1.65x for natural comprehension
                    if speed >= 1.05:
                        stretched_wav = os.path.join(temp_dir, f"seg_{idx}_stretched.wav")
                        self._stretch_wav(seg_wav, stretched_wav, speed)
                        dur, frames = self._get_wav_duration_and_frames(stretched_wav)

                # Paste into master buffer at start_sec
                start_byte = int(start_sec * self.sample_rate) * self.frame_size
                end_byte = start_byte + len(frames)

                if end_byte > len(master_buffer):
                    # Extend buffer if needed
                    master_buffer.extend(bytearray(end_byte - len(master_buffer)))

                target_chunk = master_buffer[start_byte:end_byte]
                # Saturating mix with audioop.add
                mixed = audioop.add(target_chunk, frames, self.sampwidth)
                master_buffer[start_byte:end_byte] = mixed

                # Update progress
                cur_prog = 10 + int(70 * (idx + 1) / total_segs)
                self.tasks[task_id]["progress"] = cur_prog
                self.tasks[task_id]["message"] = f"Đã lồng tiếng câu {idx+1}/{total_segs} ({int((idx+1)/total_segs*100)}%)"

            # 4. Save master audio track
            self.tasks[task_id]["progress"] = 82
            self.tasks[task_id]["message"] = "Đang tổng hợp track audio lồng tiếng hoàn chỉnh..."

            master_wav = os.path.join(temp_dir, "master_dub.wav")
            with wave.open(master_wav, "wb") as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(self.sampwidth)
                wf.setframerate(self.sample_rate)
                wf.writeframes(master_buffer)

            # Export standalone dubbed MP3
            base_name = os.path.splitext(video_path)[0]
            dubbed_mp3_path = f"{base_name}.dubbed.mp3"
            ffmpeg_exe = self.get_ffmpeg_exe()

            cmd_mp3 = [
                ffmpeg_exe, "-y",
                "-i", master_wav,
                "-codec:a", "libmp3lame",
                "-b:a", "192k",
                dubbed_mp3_path
            ]
            subprocess.run(cmd_mp3, check=True)

            # 5. FFmpeg Mux with Video & Ducking
            self.tasks[task_id]["progress"] = 88
            self.tasks[task_id]["message"] = "Đang hòa trộn âm thanh ducking và ghép vào video..."

            dubbed_video_path = f"{base_name}.dubbed.mp4"
            has_orig_audio = self.check_video_has_audio(video_path)

            if has_orig_audio and bg_volume > 0.001:
                # Duck original background audio and overlay dubbed voice
                filter_complex = (
                    f"[0:a]volume={bg_volume:.2f}[bg];"
                    f"[1:a]volume={voice_volume:.2f}[fg];"
                    f"[bg][fg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
                )
                cmd_mux = [
                    ffmpeg_exe, "-y",
                    "-i", video_path,
                    "-i", dubbed_mp3_path,
                    "-filter_complex", filter_complex,
                    "-map", "0:v",
                    "-map", "[aout]",
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-movflags", "+faststart",
                    dubbed_video_path
                ]
            else:
                # Silenced original audio / voice-only track
                filter_complex = f"[1:a]volume={voice_volume:.2f}[aout]"
                cmd_mux = [
                    ffmpeg_exe, "-y",
                    "-i", video_path,
                    "-i", dubbed_mp3_path,
                    "-filter_complex", filter_complex,
                    "-map", "0:v",
                    "-map", "[aout]",
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    "-movflags", "+faststart",
                    dubbed_video_path
                ]

            res_mux = subprocess.run(cmd_mux, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res_mux.returncode != 0:
                err_msg = res_mux.stderr.decode("utf-8", errors="ignore")
                raise RuntimeError(f"Lỗi FFmpeg khi ghép video lồng tiếng: {err_msg}")

            self.tasks[task_id]["status"] = "completed"
            self.tasks[task_id]["progress"] = 100
            self.tasks[task_id]["message"] = "Lồng tiếng video thành công!"
            self.tasks[task_id]["dubbed_video"] = dubbed_video_path
            self.tasks[task_id]["dubbed_audio"] = dubbed_mp3_path

            return {
                "status": "success",
                "dubbed_video": dubbed_video_path,
                "dubbed_audio": dubbed_mp3_path
            }

        except Exception as e:
            self.tasks[task_id]["status"] = "error"
            self.tasks[task_id]["error"] = str(e)
            self.tasks[task_id]["message"] = f"Thất bại: {str(e)}"
            raise e
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
