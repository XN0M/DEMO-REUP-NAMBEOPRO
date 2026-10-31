# Kiểm tra trước khi push — 2026-10-31

## Kết quả

- 181 kiểm thử Python đạt, 0 lỗi, 0 bỏ qua; chạy cả trên workspace và bản xuất độc lập từ Git index.
- Bản index khởi động/import `server` thành công, không cần file nguồn ngoài commit.
- Sáu bộ kiểm thử giao diện đạt: Studio 5 bước/4 ô, bố cục Studio cũ, logo/watermark, cắt video, Facebook nhiều Page và Kênh.
- Hai bộ kiểm thử JavaScript đạt: đồng bộ preview lồng tiếng và thông báo xuất video.
- Tất cả file JavaScript trong `web` qua `node --check`; `python scripts/verify_local.py` đạt.
- Kiểm tra staged không chứa đường dẫn config.json, instance.json, data hoặc cookie; không phát hiện mẫu credential phổ biến. Đây không phải chứng nhận bảo mật đầy đủ.

## Vấn đề phát hiện và xử lý

1. Bản HEAD trước đợt kiểm tra thiếu các module nguồn mà server đang import, gồm đường dẫn ứng dụng, Kênh và YouTube. Import bản archive tái hiện `ModuleNotFoundError: core.app_paths`. Đã bổ sung nguồn và kiểm thử liên quan theo phê duyệt của người dùng, rồi chạy lại 181 kiểm thử trên bản index độc lập.
2. Kịch bản UI Kênh vẫn thao tác ô token Facebook cũ đã ẩn và giả lập API đăng cũ. Đã cập nhật kịch bản tương thích, chọn Page đích rõ ràng và xác nhận kết quả đăng theo giao diện nhiều Page hiện tại. Không cần sửa logic sản phẩm cho lỗi kịch bản này.

## Giới hạn và việc còn lại

- Dùng dữ liệu tạm, media tổng hợp và mock; không tải video thật, gọi API trả phí, đăng Facebook thật, chạy Whisper/TTS model thật hoặc benchmark GPU.
- Chưa kết luận được khả năng truy cập thực tế Douyin/YouTube/Facebook ở các mạng và tài khoản khác nhau.
- Script UI VieNeu cũ chưa được dùng làm tiêu chí nghiệm thu: còn nhãn điều hướng cũ và khởi động lifespan. Các hợp đồng upload giọng, cache, tài nguyên và worker được kiểm tra trong bộ Python; chưa thay thế kiểm thử nghe giọng thật.
- Phần dọn dung lượng còn các hạng mục đã ghi nhận trước đó: phân nhóm giữ/xóa/mất khả năng sửa trên UI và lấy lại phương án dọn sau khởi động lại. Các kiểm thử lõi dọn dữ liệu hiện có đạt.
- Không có bộ kiểm thử tự động nào bảo đảm ứng dụng không còn mọi lỗi tiềm ẩn.

Log và ảnh kiểm thử nằm trong `.runtime/`, không đưa dữ liệu chạy thử lên GitHub.
