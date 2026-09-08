# 🚀 Nambeo Pro - Douyin & Facebook AI Video Automation Suite

Bộ công cụ tự động hóa xử lý video toàn diện: Tải video không logo, nhận diện giọng nói (STT), dịch thuật ngữ cảnh bằng AI và lồng tiếng tự động (TTS + Audio Ducking).

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
