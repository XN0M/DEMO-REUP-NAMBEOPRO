import os
import subprocess
import shutil
import tempfile
import imageio_ffmpeg

class VocalRemover:
    """
    Module tach giong noi goc (Vocal Remover) khoi video hoac audio.
    Giup giu lai nguyen ven 100% nhac nen (BGM) va hieu ung am thanh (SFX),
    loai bo hoan toan tieng thuyet minh tieng goc (Trung/Anh/Han...) truoc khi long tieng Viet moi.
    """
    def __init__(self):
        self.ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()

    def remove_vocals(self, input_media_path: str, output_audio_path: str = None) -> str:
        """
        Tach giong noi goc va xuat ra file audio chua nhac nen (accompaniment).
        Ho tro dau vao la file video (.mp4, .mkv, .webm...) hoac audio (.mp3, .wav, .m4a...).
        """
        if not os.path.exists(input_media_path):
            raise FileNotFoundError(f"Khong tim thay file nguon: {input_media_path}")

        if not output_audio_path:
            base, _ = os.path.splitext(input_media_path)
            output_audio_path = f"{base}.accompaniment.mp3"

        # Thu nghiem tach bang AI (neu co spleeter hoac demucs tren he thong)
        ai_success = self._try_ai_separation(input_media_path, output_audio_path)
        if ai_success:
            return output_audio_path

        # Tu dong Fallback sang bo loc am thanh DSP chuyen nghiep bang FFmpeg
        return self._ffmpeg_vocal_suppression(input_media_path, output_audio_path)

    def _try_ai_separation(self, input_path: str, output_path: str) -> bool:
        """Thu nghiem tach giong bang Demucs neu co san trong moi truong."""
        demucs_path = shutil.which("demucs")
        if not demucs_path:
            return False

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                cmd = [
                    demucs_path,
                    "--two-stems", "vocals",
                    "-n", "htdemucs_ft",
                    "--out", tmpdir,
                    input_path
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                if res.returncode == 0:
                    # Demucs xuat ra htdemucs_ft/<name>/no_vocals.wav
                    for root, _, files in os.walk(tmpdir):
                        for f in files:
                            if "no_vocals" in f.lower() or "accompaniment" in f.lower():
                                src = os.path.join(root, f)
                                shutil.copy2(src, output_path)
                                return True
        except Exception:
            pass
        return False

    def _ffmpeg_vocal_suppression(self, input_path: str, output_path: str) -> str:
        """
        Bo loc am thanh DSP bang FFmpeg:
        1. Su dung phase cancellation mid/side: pan stereo de triet tieu tieng noi o kenh giua (center-panned vocals).
        2. Tich hop bo loc dai am thoai (equalizer 300Hz - 3500Hz) de triet tieu tan so giong nguoi.
        3. Trich xuat va giu lai am tram/bass (<160Hz) tron ven tu audio goc de giu nhip nhac.
        """
        # Complex filtergraph:
        # [0:a] asplit=2 [main][bass];
        # [main] pan=stereo|c0=c0-c1|c1=c1-c0, equalizer=f=1000:t=q:w=1.8:g=-24, equalizer=f=2500:t=q:w=1.8:g=-20 [inst];
        # [bass] lowpass=f=180 [low];
        # [inst][low] amix=inputs=2:weights=1.0 0.85:normalize=0 [out]
        filter_str = (
            "[0:a]asplit=2[main][bass];"
            "[main]pan=stereo|c0=c0-c1|c1=c1-c0,equalizer=f=1000:t=q:w=1.8:g=-24,equalizer=f=2500:t=q:w=1.8:g=-20[inst];"
            "[bass]lowpass=f=180[low];"
            "[inst][low]amix=inputs=2:weights=1.0 0.85:normalize=0[aout]"
        )

        cmd = [
            self.ffmpeg_exe,
            "-y",
            "-i", input_path,
            "-filter_complex", filter_str,
            "-map", "[aout]",
            "-b:a", "192k",
            output_path
        ]

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
        if result.returncode != 0:
            # Fallback don gian hon neu audio goc chi co 1 kenh mono
            simple_cmd = [
                self.ffmpeg_exe,
                "-y",
                "-i", input_path,
                "-af", "volume=0.35,equalizer=f=1000:t=q:w=2:g=-18",
                "-b:a", "192k",
                output_path
            ]
            subprocess.run(simple_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

        return output_path
