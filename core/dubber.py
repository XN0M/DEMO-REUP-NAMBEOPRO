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
from core.vocal_remover import VocalRemover
from core.tts_runtime import runtime as vieneu_runtime, library as voice_library, GPU_LOCK, TTSError
from core.studio_media import speech_tempo

try:
    import audioop
except ImportError:
    import audioop_lts as audioop
    import sys
    sys.modules['audioop'] = audioop

import edge_tts
from aiohttp import ClientConnectionError, ClientResponseError
from core.tts_runtime import ProviderUnavailable

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
    },
    {
        "id": "omnivoice",
        "name": "OmniVoice (AI Auto-Clone Giọng Gốc)",
        "gender": "neutral",
        "lang": "multilingual",
        "provider": "omnivoice",
        "free": True
    }
]

class DouyinDubber:
    def __init__(self):
        self.tasks: Dict[str, Dict] = {}
        self.sample_rate = 44100
        self.channels = 2
        self.sampwidth = 2 # 16-bit
        self.frame_size = self.channels * self.sampwidth
        self.omnivoice_model = None
        vieneu_runtime.gpu_release = self.release_omnivoice

    def release_omnivoice(self):
        if self.omnivoice_model is not None:
            self.omnivoice_model = None
            import gc
            gc.collect()
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

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
        try:
            presets = vieneu_runtime.catalog(load=vieneu_runtime.python.exists() and not vieneu_runtime.install_process)
        except (TTSError, OSError):
            presets = []
        custom = [dict(id='vieneu:custom:'+v['id'], name=v['name'], provider='vieneu', model='v3turbo',
                       gender='neutral', lang='vi-VN', free=True, kind='custom') for v in voice_library.list()]
        return [*VOICE_CATALOG, *presets, *custom]

    async def synthesize_edge(self, text: str, voice: str = "vi-VN-HoaiMyNeural", retries: int = 3) -> bytes:
        """Synthesize text using Microsoft Edge TTS with automatic retries."""
        if tuple(edge_tts.__version_info__) < (7, 3, 1):
            raise ProviderUnavailable('edge', 'Edge TTS đang dùng phiên bản cũ gây lỗi HTTP 403. '
                'Chạy cap_nhat.bat rồi khởi động lại tool; hoặc chọn giọng VieNeu local.')
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
                if isinstance(e, ClientResponseError) and e.status in (401, 403):
                    raise ProviderUnavailable('edge', f'Edge TTS bị dịch vụ từ chối (HTTP {e.status}). '
                        'Chạy cap_nhat.bat rồi khởi động lại tool. Nếu vẫn lỗi, chọn giọng VieNeu local.') from None
                if attempt == retries - 1:
                    if isinstance(e, (ClientConnectionError, TimeoutError)):
                        raise ProviderUnavailable('edge', 'Không kết nối được Edge TTS. '
                            'Kiểm tra mạng rồi thử lại, hoặc chọn giọng VieNeu local.') from None
                    raise TTSError(f'Edge TTS không tạo được âm thanh cho giọng {voice} sau {retries} lần thử. '
                                   'Kiểm tra lời đọc và chọn lại giọng.') from None
                await asyncio.sleep(0.5 * (attempt + 1))
        raise TTSError(f'Edge TTS không trả về âm thanh cho giọng {voice}. Kiểm tra lời đọc rồi thử lại.')

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

    def _get_omnivoice_model(self):
        if self.omnivoice_model is None:
            try:
                import torch
                from omnivoice import OmniVoice
            except ImportError:
                raise RuntimeError("Thư viện omnivoice chưa được cài đặt. Vui lòng chạy lại file cài đặt (cai_dat.bat) hoặc: pip install omnivoice torch soundfile")
            
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            print(f"[OmniVoice] Loading model on {device}...")
            self.omnivoice_model = OmniVoice.from_pretrained(
                "k2-fsa/OmniVoice",
                device_map=device,
                dtype=torch.float16 if torch.cuda.is_available() else torch.float32
            )
        return self.omnivoice_model

    async def synthesize_omnivoice(self, text: str, ref_audio: str = None) -> bytes:
        import soundfile as sf
        import io
        import asyncio

        if not ref_audio or not os.path.exists(ref_audio):
            raise RuntimeError("OmniVoice yêu cầu file âm thanh tham chiếu (ref_audio) để clone giọng.")

        loop = asyncio.get_running_loop()

        def _generate_unlocked():
            model = self._get_omnivoice_model()
            # OmniVoice generate() returns a list of np.ndarray
            audio_arrays = model.generate(
                text=text,
                ref_audio=ref_audio
            )
            if audio_arrays and len(audio_arrays) > 0:
                wav_io = io.BytesIO()
                sf.write(wav_io, audio_arrays[0], 24000, format='WAV')
                return wav_io.getvalue()
            return b""

        def _generate():
            with GPU_LOCK:
                if vieneu_runtime.install_process and vieneu_runtime.install_process.poll() is None:
                    raise TTSError('Đang cài VieNeu; hãy chờ trước khi dùng GPU')
                vieneu_runtime.stop()
                return _generate_unlocked()
        return await loop.run_in_executor(None, _generate)

    async def synthesize_vieneu(self, text: str, voice: str, cancelled=lambda: False, refresh=False) -> bytes:
        return await asyncio.to_thread(vieneu_runtime.synthesize, text, voice, cancelled, refresh)

    async def synthesize_sentence(
        self,
        text: str,
        voice: str = "vi-VN-HoaiMyNeural",
        provider: str = "edge",
        api_key: str = "",
        base_url: str = "",
        ref_audio: str = None,
        cancelled=lambda: False,
        refresh=False
    ) -> bytes:
        """Synthesize a single sentence with auto provider detection."""
        text = text.strip()
        if not text:
            return b""

        if voice.startswith('vieneu:') or provider == 'vieneu':
            return await self.synthesize_vieneu(text, voice, cancelled, refresh)
        if provider == "omnivoice" or voice == "omnivoice":
            return await self.synthesize_omnivoice(text, ref_audio)
        elif voice in {v['id'] for v in VOICE_CATALOG if v['provider'] == 'openai'}:
            return await self.synthesize_openai(text, voice, api_key, base_url)
        elif voice.startswith("vi-VN-") or provider == "edge":
            return await self.synthesize_edge(text, voice)
        elif provider == 'openai':
            return await self.synthesize_openai(text, voice, api_key, base_url)
        raise TTSError('Provider TTS không hợp lệ')

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
        """Retiming speech with the bundled FFmpeg's Rubber Band filter."""
        ffmpeg_exe = self.get_ffmpeg_exe()
        cmd = [
            ffmpeg_exe, "-y",
            "-i", input_wav,
            "-filter:a", speech_tempo(speed_factor),
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

    def _extract_ref_audio(self, video_path: str, start_sec: float, end_sec: float, output_wav: str):
        """Extract a segment of audio to use as reference for voice cloning."""
        ffmpeg_exe = self.get_ffmpeg_exe()
        cmd = [
            ffmpeg_exe, "-y",
            "-i", video_path,
            "-ss", str(start_sec),
            "-to", str(end_sec),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "24000",
            "-ac", "1",
            output_wav
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if res.returncode != 0:
            print(f"Lỗi extract_ref_audio: {res.stderr.decode('utf-8', errors='ignore')}")

    async def execute_dubbing(
        self,
        video_path: str,
        transcript_data: dict,
        mode: str = "single",
        single_voice: str = "vi-VN-HoaiMyNeural",
        character_voices: Optional[Dict[str, str]] = None,
        bg_volume: float = 0.15,
        voice_volume: float = 1.2,
        clean_bgm: bool = False,
        openai_api_key: str = "",
        openai_base_url: str = "",
        task_id: str = "",
        video_speed: float = 1.0
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
        video_speed = float(video_speed)
        if not .5 <= video_speed <= 2:
            raise ValueError('Tốc độ video phải nằm trong 0,5–2×')
        self.tasks[task_id] = {
            "status": "processing",
            "progress": 5,
            "message": "Bắt đầu chuẩn bị phụ đề và giọng đọc...",
            "error": None,
            "dubbed_video": None,
            "dubbed_audio": None
        }

        # Stage beside the source so final os.replace stays on the same volume.
        temp_dir = tempfile.mkdtemp(prefix=".dub_", dir=os.path.dirname(os.path.abspath(video_path)))
        voice_lease = voice_library.pin([single_voice, *character_voices.values()])
        leased = False
        try:
            voice_lease.__enter__()
            leased = True
            # 1. Prepare segments
            all_segments = transcript_data.get("segments", [])
            valid_segments = []
            for s in all_segments:
                txt = s.get('dub_text') if s.get('dub_text') is not None else s.get("vietnamese") or s.get("chinese") or s.get("text", "")
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
            total_duration = max(video_dur, max_seg_end)

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

            # 3. Synthesize first; measured lengths determine the shared playback schedule.
            prepared = []
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
                        if single_voice.startswith('vieneu:'):
                            voice = single_voice
                        elif gender == "male":
                            voice = "vi-VN-NamMinhNeural"
                        elif gender == "female":
                            voice = "vi-VN-HoaiMyNeural"
                        else:
                            voice = single_voice
                else:
                    voice = single_voice

                # Synthesize
                if voice.startswith('vieneu:'):
                    provider = 'vieneu'
                elif voice == "omnivoice":
                    provider = "omnivoice"
                else:
                    provider = "openai" if voice in ["alloy", "echo", "fable", "onyx", "nova", "shimmer"] else "edge"
                    
                seg_mp3 = os.path.join(temp_dir, f"seg_{idx}.mp3")
                seg_wav = os.path.join(temp_dir, f"seg_{idx}.wav")

                ref_audio_path = None
                if provider == "omnivoice":
                    # Extract original audio of this segment to use as zero-shot reference
                    ref_audio_path = os.path.join(temp_dir, f"ref_{idx}.wav")
                    # Try to get a slightly longer segment for better cloning if it's too short, but cap at video boundaries
                    clone_start = max(0, start_sec - 0.5)
                    clone_end = min(total_duration, end_sec + 0.5)
                    self._extract_ref_audio(video_path, clone_start, clone_end, ref_audio_path)

                audio_data = await self.synthesize_sentence(
                    text=text,
                    voice=voice,
                    provider=provider,
                    api_key=openai_api_key,
                    base_url=openai_base_url,
                    ref_audio=ref_audio_path
                )

                with open(seg_mp3, "wb") as f:
                    f.write(audio_data)

                # Convert to standard WAV
                self._convert_mp3_to_wav(seg_mp3, seg_wav)

                # Save raw duration; alignment uses the following cue and available silence.
                dur, frames = self._get_wav_duration_and_frames(seg_wav)
                prepared.append({'id': str(idx), 'start': start_sec, 'end': end_sec,
                                 'duration': dur, 'path': seg_wav})

                # Update progress
                cur_prog = 10 + int(60 * (idx + 1) / total_segs)
                self.tasks[task_id]["progress"] = cur_prog
                self.tasks[task_id]["message"] = f"Đã tạo giọng câu {idx+1}/{total_segs}"

            from .dub_timing import schedule
            play = schedule(prepared, video_speed=video_speed, video_end=video_dur)
            self.tasks[task_id]['timing'] = play
            self.tasks[task_id]['extended_seconds'] = max(0, max(p['play_end'] for p in play.values()) - video_dur)
            latest_audio_end = 0.0
            for idx, item in enumerate(prepared):
                timing = play[item['id']]
                seg_wav = item['path']
                tempo_factor = timing['speed'] / video_speed
                if abs(tempo_factor - 1) > .001:
                    stretched_wav = os.path.join(temp_dir, f"seg_{idx}_stretched.wav")
                    self._stretch_wav(seg_wav, stretched_wav, tempo_factor)
                    seg_wav = stretched_wav
                _, frames = self._get_wav_duration_and_frames(seg_wav)
                # Leave headroom before adding two concurrent 16-bit voices.
                frames = audioop.mul(frames, self.sampwidth, .5)
                start_byte = int(timing['play_start'] * self.sample_rate) * self.frame_size
                end_byte = start_byte + len(frames)
                latest_audio_end = max(latest_audio_end, end_byte / self.frame_size / self.sample_rate)

                if end_byte > len(master_buffer):
                    # Extend buffer if needed
                    master_buffer.extend(bytearray(end_byte - len(master_buffer)))

                target_chunk = master_buffer[start_byte:end_byte]
                # Saturating mix with audioop.add
                mixed = audioop.add(target_chunk, frames, self.sampwidth)
                master_buffer[start_byte:end_byte] = mixed

                self.tasks[task_id]["progress"] = 70 + int(10 * (idx + 1) / total_segs)
                self.tasks[task_id]["message"] = f"Đã căn giọng câu {idx+1}/{total_segs}"

            self.tasks[task_id]['extended_seconds'] = max(
                self.tasks[task_id]['extended_seconds'], latest_audio_end - video_dur)

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
            final_mp3_path = f"{base_name}.dubbed.mp3"
            dubbed_mp3_path = os.path.join(temp_dir, 'output.mp3')
            ffmpeg_exe = self.get_ffmpeg_exe()

            cmd_mp3 = [
                ffmpeg_exe, "-y",
                "-i", master_wav,
                "-codec:a", "libmp3lame",
                "-b:a", "192k",
                dubbed_mp3_path
            ]
            subprocess.run(cmd_mp3, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # 5. FFmpeg Mux with Video & Ducking
            self.tasks[task_id]["progress"] = 88
            self.tasks[task_id]["message"] = "Đang hòa trộn âm thanh ducking và ghép vào video..."

            final_video_path = f"{base_name}.dubbed.mp4"
            dubbed_video_path = os.path.join(temp_dir, 'output.mp4')
            has_orig_audio = self.check_video_has_audio(video_path)

            bg_audio_file = video_path
            if clean_bgm and has_orig_audio:
                try:
                    self.tasks[task_id]["message"] = "Đang tách sạch giọng nói gốc để giữ nhạc nền..."
                    clean_inst_path = os.path.join(temp_dir, "clean_bgm.mp3")
                    v_remover = VocalRemover()
                    bg_audio_file = v_remover.remove_vocals(video_path, clean_inst_path)
                except Exception as ve:
                    print(f"Lỗi tách giọng, dùng audio gốc: {ve}")
                    bg_audio_file = video_path

            if has_orig_audio and bg_volume > 0.001:
                # Duck background audio (clean or original) and overlay dubbed voice
                if bg_audio_file != video_path:
                    # Input 0: video, Input 1: dubbed voice, Input 2: clean bgm
                    filter_complex = (
                        f"[2:a]volume={bg_volume:.2f}[bg];"
                        f"[1:a]volume={voice_volume:.2f}[fg];"
                        f"[bg][fg]amix=inputs=2:duration=longest:dropout_transition=2[aout]"
                    )
                    cmd_mux = [
                        ffmpeg_exe, "-y",
                        "-i", video_path,
                        "-i", dubbed_mp3_path,
                        "-i", bg_audio_file,
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
                    filter_complex = (
                        f"[0:a]volume={bg_volume:.2f}[bg];"
                        f"[1:a]volume={voice_volume:.2f}[fg];"
                        f"[bg][fg]amix=inputs=2:duration=longest:dropout_transition=2[aout]"
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

            tail = self.tasks[task_id]['extended_seconds']
            if tail > .01:
                graph = cmd_mux[cmd_mux.index('-filter_complex') + 1]
                cmd_mux[cmd_mux.index('-filter_complex') + 1] = graph + f';[0:v]tpad=stop_mode=clone:stop_duration={tail:.6f}[vout]'
                cmd_mux[cmd_mux.index('-map') + 1] = '[vout]'
                cmd_mux[cmd_mux.index('-c:v') + 1] = 'libx264'
                cmd_mux[cmd_mux.index('-c:v') + 2:cmd_mux.index('-c:v') + 2] = ['-preset', 'fast', '-crf', '20']
            cmd_mux[-1:-1] = ['-t', str(video_dur + tail)]
            res_mux = subprocess.run(cmd_mux, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res_mux.returncode != 0:
                err_msg = res_mux.stderr.decode("utf-8", errors="ignore")
                raise RuntimeError(f"Lỗi FFmpeg khi ghép video lồng tiếng: {err_msg}")

            # Publish only after successful mux; retain previous outputs on failure.
            os.replace(dubbed_video_path, final_video_path)
            os.replace(dubbed_mp3_path, final_mp3_path)
            from .dub_timing import captions
            from .transcriber import DouyinTranscriber
            cues = [{**seg, 'id': str(index)} for index, seg in enumerate(valid_segments)]
            windows = captions(cues, {'render': {'dubbed': True, 'speed': video_speed}, 'caption_hold': 12},
                               {key: {'status': 'ready', **value} for key, value in play.items()})
            for lang in ('vi', 'bilingual'):
                subtitle_cues = []
                for seg in cues:
                    original = seg.get('chinese') or seg.get('text') or ''
                    spoken = seg.get('vietnamese') or seg.get('dub_text') or original
                    caption = spoken if lang == 'vi' else '\n'.join(filter(None, [spoken, original]))
                    a, b = windows[seg['id']]
                    if caption and b > a:
                        subtitle_cues.append({'start': a, 'end': b, 'text': caption})
                staged_srt = os.path.join(temp_dir, f'{lang}.srt')
                with open(staged_srt, 'w', encoding='utf-8') as stream:
                    stream.write(DouyinTranscriber.generate_srt(subtitle_cues, max_hold=12*video_speed))
                os.replace(staged_srt, f'{base_name}.dubbed.{lang}.srt')
            dubbed_video_path, dubbed_mp3_path = final_video_path, final_mp3_path
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
            if leased:
                voice_lease.__exit__(None, None, None)
            shutil.rmtree(temp_dir, ignore_errors=True)
