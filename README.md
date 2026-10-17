# 🚀 Nambeo Pro - Douyin & Facebook AI Video Automation Suite

Bộ công cụ tự động hóa xử lý video toàn diện: Tải video không logo, nhận diện giọng nói (STT), dịch thuật ngữ cảnh bằng AI và lồng tiếng tự động (TTS + Audio Ducking).

### Quy trình Studio

Trong **Thư viện**, bấm **Mở Studio** để làm việc theo bốn tab: **Bản thoại & Dịch → Phụ đề → Lồng tiếng → Xuất video**. Video mới có nút **Tạo bản thoại** ngay trong Studio; sau khi nhận diện, bấm **Dịch N câu sang tiếng Việt**, duyệt đề xuất rồi áp dụng. Bảng nhân vật và cấu hình AI có ngay tại tab này. Video tiếng Việt dùng lời thoại gốc.

Lồng tiếng là tùy chọn. Khi xuất, chọn **Giữ âm thanh gốc** hoặc **Dùng bản lồng tiếng**, bật/tắt phụ đề riêng. Các thao tác nhận diện lại và dịch lại giữ bản hiện tại đến khi duyệt; nhận diện lại tạo phiên bản có thể khôi phục. Waveform chỉ chạy khi bấm tải. Nếu hủy nhận diện khi model đang nạp hoặc xử lý một đoạn, ứng dụng chờ phần đó dừng an toàn trước khi nhả khóa video.

Tiến độ, thử lại và kết quả nằm ngay trong Studio. Kết quả từ phiên bản cũ không tự ghi đè nội dung đang sửa. API nhận diện/dịch cũ vẫn được giữ; công cụ xuất nâng cao cũ vẫn có trong menu video.

---

## 📂 Cấu Trúc Mã Nguồn (Architecture Overview)

Dự án được thiết kế theo kiến trúc Module hóa (Modular Architecture), phân tách độc lập từng luồng xử lý:

```
nambeopro/
├── core/                       # Các module xử lý logic lõi (Core Engines)
│   ├── extractor.py            # Bóc tách link video Douyin (qua Web API & Playwright)
│   ├── extractor_fb.py         # Bóc tách link video Facebook (Reels / Watch)
│   ├── downloader.py           # Tải video stream, xuất thumbnail với OpenCV
│   ├── transcriber.py          # Chuyển đổi giọng nói thành phụ đề (Faster-Whisper STT)
│   ├── translator.py           # Dịch thuật phụ đề bằng AI (Gemini / OpenAI)
│   └── dubber.py               # Lồng tiếng tự nhiên (Edge-TTS / OpenAI) & Mix âm thanh FFmpeg
├── web/
│   └── index.html              # Frontend giao diện người dùng (TailwindCSS + Lucide Icons)
├── downloads/                  # Thư mục lưu trữ video và file kết quả
├── config.json                 # Cấu hình API Key (Gemini, OpenAI) và model mặc định
├── requirements.txt            # Danh sách thư viện phụ thuộc Python
├── server.py                   # FastAPI REST API Backend phục vụ giao diện và tiến trình
├── start_app.py                # Script khởi chạy (Tự giải phóng port 8000, mở trình duyệt)
├── cai_dat.bat                 # Script 1-click cài đặt môi trường venv và thư viện
├── run.bat                     # Script 1-click chạy ứng dụng
└── HUONG_DAN_SU_DUNG.txt       # Tài liệu hướng dẫn sử dụng nhanh cho người dùng
```

---

## 🛠 Hướng Dẫn Nâng Cấp & Mở Rộng Luồng (For Developers)

Mã nguồn được viết tường minh, dễ dàng bảo trì và mở rộng thêm các luồng xử lý mới:

### 1. Thêm Nền Tảng Video Mới (TikTok Quốc Tế, YouTube Shorts, Instagram...)
- **Bước 1**: Tạo module mới trong thư mục `core/` (ví dụ: `core/extractor_tiktok.py` hoặc `core/extractor_yt.py`).
- **Bước 2**: Khai báo hàm trích xuất trả về metadata chuẩn gồm:
  ```python
  {
      "id": "video_id",
      "title": "Tiêu đề video",
      "video_url": "URL tải video trực tiếp",
      "cover_url": "URL ảnh thumbnail",
      "author": "Tên kênh/tác giả",
      "duration": 60
  }
  ```
- **Bước 3**: Thêm endpoint tiếp nhận URL trong `server.py` và gọi hàm trích xuất tương ứng.

### 2. Tùy Biến Prompt Dịch Thuật AI Trong `core/translator.py`
- Hàm `translate_subtitles()` tiếp nhận danh sách các đoạn phụ đề kèm thời gian (`start`, `end`, `text`).
- Bạn có thể tùy chỉnh `system_prompt` để AI dịch theo các phong cách khác nhau:
  - Phong cách phim cổ trang / kiếm hiệp / ngôn tình.
  - Phong cách review phim kịch tính, lôi cuốn.
  - Thêm từ điển thuật ngữ chuyên ngành (Glossary / Terminology mapping).

### 3. Thêm Giọng Đọc Mới Trong `core/dubber.py`
- Danh mục giọng đọc nằm ở mảng `VOICE_CATALOG` đầu file `core/dubber.py`.
- Để thêm giọng Edge-TTS mới, chỉ cần khai báo ID giọng tương ứng từ Microsoft Edge TTS:
  ```python
  {
      "id": "vi-VN-HoaiMyNeural",
      "name": "Hoài My (Nữ - Truyền cảm)",
      "gender": "female",
      "lang": "vi-VN",
      "provider": "edge",
      "free": True
  }
  ```
- Bạn cũng có thể tích hợp thêm các dịch vụ TTS khác như ElevenLabs, Vbee, FPT AI bằng cách mở rộng class `DouyinDubber`.

### 4. Tùy Chỉnh Cơ Chế Hòa Âm (Audio Ducking)
- Trong `core/dubber.py`, hàm `mix_audio()` sử dụng `imageio_ffmpeg` để:
  - Tách riêng audio gốc của video.
  - Giảm âm lượng nền của video gốc (mặc định còn 15% - 20%) khi có tiếng lồng tiếng.
  - Tự động điều chỉnh tốc độ nói (speech rate) để phụ đề tiếng Việt khớp hoàn hảo với thời lượng từng phân cảnh gốc.

---

## ⚡ Yêu Cầu Kỹ Thuật
- Python >= 3.10
- FFmpeg (Được tích hợp sẵn thông qua package `imageio-ffmpeg`, không cần cài đặt thêm)
- Playwright Chromium (Tự động tải khi chạy `cai_dat.bat`)
- yt-dlp (Cài tự động qua `requirements.txt`, dùng để tải video YouTube)


## Studio phụ đề & lồng tiếng

Trong **Lịch sử đã tải**, chọn **Studio phụ đề & lồng tiếng**. Nếu video chưa có
bản thoại, dùng nút **Bản thoại** để nhận diện trước. Studio đọc transcript cũ
mà không tự chuyển đổi dữ liệu trên ổ đĩa.

- Sửa nội dung gốc, bản dịch, lời đọc riêng và người nói; chọn câu để tạo giọng
  hoặc nhận đề xuất dịch/rút gọn. Đề xuất chỉ thay nội dung sau khi được duyệt.
- Bấm timeline để tua, kéo hai đầu cue để đổi thời gian. Tách tại đầu phát và
  con trỏ văn bản; gộp câu khác người nói sẽ yêu cầu chọn người nói mới.
- Chỉnh font DejaVu Sans đóng gói, màu/viền/nền, song ngữ và vùng che riêng.
  Preview tương tác là bản gần đúng; **Render thử 5 giây** dùng cùng ASS/font/
  filter như xuất cuối. Softsub MP4 không bảo toàn đầy đủ style; tải ASS hoặc
  xuất hardsub để giữ hình thức.
- Lưu style theo video, preset cá nhân lưu trong trình duyệt. Giữ 20 phiên bản
  đã lưu, cùng backup transcript trước lần lưu đầu. Khôi phục tạo revision mới.
- TTS chỉ tạo lại nội dung/giọng thay đổi, căn thời gian từ audio cache khi kéo
  cue. Giới hạn hai yêu cầu đồng thời; tăng tốc tối đa 1,15×. Câu quá dài phải
  được rút gọn hoặc kéo dài thời gian, không cắt đuôi audio.
- Bật **Dùng bản lồng tiếng** để phối giọng và nền khi xuất. Bản xuất hoàn chỉnh
  bị chặn khi thiếu giọng; bản thử có lựa chọn bỏ qua câu lỗi rõ ràng.
- Job lưu SQLite ở `.runtime/studio.sqlite3`; job bị gián đoạn sau restart có
  thể chạy lại bằng cache. **Lịch sử job** cho phép xem các bản render trước.
- Batch ưu tiên cấu hình của video đã được lưu trong Studio; video còn lại giữ
  luồng batch cũ. Batch Studio tạo phần audio còn thiếu trước khi render.

Transcript `.transcript.json` là dữ liệu chuẩn, có ID câu và revision.
`.studio.json` là bản phản chiếu cấu hình có thể dựng lại; `.studio-assets/`
chứa phiên bản, manifest, audio cache và các bản xuất riêng theo revision.
Chuyển/xóa nguồn qua ứng dụng sẽ bao gồm các tài nguyên này. Không sửa/xóa
cache thủ công trong khi job đang chạy. Các bản xuất cũ được giữ khi render lỗi.
Không đưa API key vào project, cache hoặc snapshot job.

API mới dùng prefix `/api/studio`: project (GET/PUT), tts, translate, shorten,
preview, export, waveform, jobs và cancel; asset và subtitles để tải kết quả.
Lưu với revision cũ trả 409; input Studio không hợp lệ trả 400; đường dẫn ngoài
thư mục tải trả 403. Các endpoint cũ tiếp tục dùng được.

Kiểm thử: `python -m unittest discover -s tests -v`. Bộ test dùng FFmpeg thật
với video mẫu và mock toàn bộ dịch vụ AI/TTS, không phát sinh chi phí API.
Font và giấy phép nằm trong `web/fonts/`.

## VieNeu-TTS local và giọng cá nhân

Trong phần chọn giọng của Studio, lồng tiếng nhanh hoặc batch, bấm **Giọng cá
nhân / Cài VieNeu**. Chọn **RTX / CUDA** cho máy có NVIDIA, rồi **Cài / sửa
VieNeu**. Hoặc chạy `cai_vieneu.bat`. Bộ cài tạo `.venv-vieneu` riêng, tải
PyTorch CUDA 12.8 và VieNeu 3.8.3, tải model rồi thử giọng có sẵn và nhân bản.
Lần đầu cần mạng, nhiều GB ổ trống và có thể mất nhiều phút. Theo dõi trạng
thái và nhật ký ngay trong cửa sổ. Cần driver hỗ trợ CUDA 12.8; nếu CUDA lỗi,
có thể chọn CPU rõ ràng. Môi trường Python của ứng dụng chính được giữ riêng.
Khi chuyển CUDA/CPU lần đầu, bấm **Cài / sửa VieNeu** để tải bộ model cho thiết
bị đó. Model đã chuẩn bị được dùng offline và cache ghi nhận phiên bản snapshot.

Sau khi cài, chọn giọng **vieneu** trong danh sách; Studio cho phép chọn riêng
theo người nói. Giọng có sẵn và giọng cá nhân dùng chung ở cả ba luồng. Tùy chọn
**Mặc định cho nhanh / batch** lưu trên trình duyệt cho các tác vụ mới; cấu hình
giọng của video đã lưu trong Studio vẫn được ưu tiên khi batch.

Thêm giọng bằng WAV/MP3/M4A/FLAC dài **3–8 giây**, tối đa **20 MB**, một người
nói rõ, ít nhạc nền. Đặt tên và xác nhận quyền sử dụng; bấm **Thử giọng** để
nghe câu mới bằng mẫu vừa lưu. Audio mẫu được chuẩn hóa WAV mono 48 kHz, lưu
cùng metadata trong `.runtime/voices`; không gửi mẫu đến dịch vụ TTS đám mây.
Giọng đang được job sử dụng không thể xóa. Nếu xóa giọng, dự án tham chiếu cần
chọn lại trước khi tạo lời mới. Sao lưu thư mục này nếu muốn giữ giọng cá nhân.

Worker chỉ lắng nghe localhost, có token riêng và tự giải phóng sau 5 phút
không dùng. VieNeu chạy lần lượt và chia sẻ lượt GPU với OmniVoice/Demucs.
Không tự đổi sang giọng hay provider khác khi gặp lỗi. Cache phụ thuộc lời
đọc, giọng, nội dung mẫu và phiên bản engine; đổi tên giọng không tạo lại câu.
VieNeu ở luồng nhanh/batch cũng giới hạn tăng tốc 1,15×; câu dài cần sửa trong
Studio. Cơ chế giảm nền/tách giọng hiện có vẫn được sử dụng.

API bổ sung: `/api/tts/status`, `/install`, `/device`, `/log` và
`/voices/custom` (GET/POST), `/voices/custom/{id}` (PATCH/DELETE),
`/voices/custom/{id}/sample` (GET), đều dưới prefix `/api/tts`.
Upload dùng body audio nhị phân với query `name`, `filename`, `consent=true`.
Giọng dùng `{provider: "vieneu", model: "v3turbo", voice: "vieneu:preset:…"}`
hoặc `voice: "vieneu:custom:<uuid>"`. `/api/dub/voices` cung cấp danh mục chung.
