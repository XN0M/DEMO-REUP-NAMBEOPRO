# Xem thử, xuất và dọn Studio

- Trong khung video, chọn **Tiếng gốc** hoặc **Lồng tiếng** để nghe thử trực tiếp theo timeline. Lựa chọn này không đổi âm thanh khi xuất.
- Câu thiếu giọng hoặc cần căn lại sẽ dừng phát. Bật **Bỏ qua câu chưa sẵn sàng** nếu chỉ cần nghe các câu hợp lệ.
- **Render thử có lồng tiếng · 5 giây** kiểm tra bản phối và kiểu phụ đề thực tế. **Nghe trong cảnh** đặt đoạn thử quanh câu được chọn.
- Trong cấu hình xuất, chọn trần tốc độ giọng 1,15× hoặc 1,25×. Dùng **Tạo giọng cho các câu cần cập nhật** để căn lại từ audio thô; câu quá dài vẫn cần rút gọn lời và duyệt đề xuất.
- Khi xuất xong, thẻ kết quả có nút xem, tải, mở thư mục và dọn dung lượng. Thẻ vẫn xuất hiện khi đã đóng Studio nhưng ứng dụng còn mở. Kết quả cũ nằm trong lịch sử xử lý.
- **Lưu gọn để sửa tiếp** giữ bản gốc, bản thoại, cấu hình, lịch sử và cache còn được tham chiếu; dọn bản thử, bản xuất cũ và cache không dùng.
- **Chỉ giữ thành phẩm** sao chép và kiểm tra video vào thư mục `finished/` trước khi xóa dữ liệu chỉnh sửa. Thư viện đánh dấu thành phẩm này để xem thay vì mở lại dự án cũ.
- Luôn xem danh sách xóa và dung lượng trước khi xác nhận. File lạ được giữ lại; file bị khóa có thể thử dọn tiếp. Không xóa model hoặc cache dùng chung.

Xem thử trực tiếp là công cụ chỉnh nhanh. Hiệu ứng ducking chính xác và kiểu chữ cuối cùng cần duyệt qua bản render. Chưa nghiệm thu độ lệch đồng bộ dưới 100 ms trên trình duyệt thực tế.


## Logo và watermark

Quy trình Studio: **Bản thoại & Dịch → Phụ đề → Thương hiệu → Lồng tiếng → Xuất video**.

Trong **Thương hiệu**, tải PNG/WebP tĩnh (tối đa 5 MB, 4096×4096), bật watermark chữ hoặc dùng cả hai. Kéo thành phần trên video để đổi vị trí; kéo góc ↘ để chỉnh chiều rộng. Các vị trí góc, kích thước, độ mờ và khoảng hiển thị có thể chỉnh riêng. Thời gian đặt theo video gốc và tự quy đổi khi trim/đổi tốc độ.

Bộ thương hiệu có tên dùng lại cho nhiều video; áp dụng bộ chỉ thay phần thương hiệu. Dự án giữ bản sao logo riêng, nên cập nhật/xóa bộ dùng chung không thay đổi dự án cũ. **Bỏ qua bước này** tắt cả logo và chữ trong video hiện tại.

Preview và bản xuất dùng chung ảnh watermark chữ, font và hình học. Thứ tự lớp: video → vùng che chữ gốc → logo → watermark chữ → phụ đề mới. Giao diện cảnh báo khi vùng thương hiệu và phụ đề đang hiển thị chạm nhau. Luôn render thử để duyệt chất lượng trước khi xuất.

Lưu gọn bảo toàn ảnh được dự án/phiên bản/tác vụ tham chiếu; chỉ giữ thành phẩm sẽ dọn tài nguyên riêng của dự án và giữ nguyên thư viện bộ thương hiệu.


## Kéo chỉnh bố cục

Giữ chuột hoặc chạm vào vùng **Kéo lên / xuống** giữa video và bảng câu, rồi kéo để đổi diện tích hai vùng. Tỷ lệ hiển thị ngay trên tay nắm; bố cục được nhớ sau khi thả. Nhấn **Esc** trong lúc kéo để hủy, nhấp đúp để về cân đối, hoặc chọn một trong ba bố cục có sẵn. Khi tay nắm được chọn bằng Tab, dùng **↑/↓** để chỉnh, **Shift + ↑/↓** để chỉnh bước lớn, **Home/End** để tới giới hạn. **Khôi phục bố cục** đưa về thiết lập mặc định.


## Đặt logo trên ảnh xem trước

Trong bước **Thương hiệu**, bấm **Đặt logo trên ảnh** (hoặc **Mở ảnh preview để đặt logo**). Khung riêng dùng một ảnh lấy từ video, không cần phát hay render video. Chọn mốc giây rồi bấm **Lấy ảnh**, tải logo ngay trong khung, kéo logo/chữ để đặt vị trí, kéo góc ↘ để đổi kích thước và bấm **Lưu vị trí**.

Ảnh được cắt đúng tỷ lệ đầu ra; đổi tỷ lệ trong khung cũng đổi tỷ lệ video khi xuất. Khi chỉnh vị trí, khung hiện tất cả logo/chữ đã bật; khoảng thời gian xuất hiện của chúng vẫn giữ theo thiết lập. Ảnh nền được trả trực tiếp, không tạo file tạm trong dự án.
