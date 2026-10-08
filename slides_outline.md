# Slide Outline - Bài 3: Pipeline làm sạch & hợp nhất đa nguồn (5-7 phút)

## Slide 1 - Bài toán và ba quyết định chính

- **Bối cảnh & Dữ liệu thực tế**:
  - Dữ liệu thu thập từ nguồn duy nhất `Chapter_4/data`: Tệp `manifest.csv` gồm 667 episode và 667 tệp replay JSON mô phỏng Kaggriculture.
  - Phạm vi thực thi: Pipeline xử lý mẫu benchmark 300 replay JSON để kiểm thử hiệu năng xử lý big data, trích xuất 432.000 lượt đấu (turn-level) và 529.922 lệnh thị trường (market orders).
- **Mục tiêu**:
  - Xây dựng pipeline tự động hóa có thể tái sử dụng, giải quyết các vấn đề đặc trưng của dữ liệu crawl thực tế: đa nguồn theo ngày, đa phiên bản engine, đa cấu hình game và dữ liệu thiếu có cấu trúc.
- **Ba quyết định kiến trúc cốt lõi**:
  1. Hợp nhất bằng khóa nghiệp vụ `episode_id`, loại bỏ hoàn toàn UUID `id`.
  2. Khử trùng lặp qua thời gian: Giữ bản ghi crawl mới nhất, lưu vết `first_seen_date`/`last_seen_date` để chống rò rỉ dữ liệu (Data Leakage).
  3. Phân tách rạch ròi lỗi kỹ thuật khỏi ngoại lai thống kê; bảo toàn ngữ nghĩa gốc (không biến missing thành zero).

---

## Slide 2 - Quyết định 1: Join bằng `episode_id` thay vì UUID `id`

- **Thực trạng dữ liệu**: Mỗi tệp JSON replay chứa hai mã định danh: `id` (chuỗi UUID nội bộ của lần export) và `info.EpisodeId` (số nguyên định danh trận đấu).
- **Chứng minh khóa chuẩn**:
  - `episode_id = filename stem = info.EpisodeId = manifest.episode_id`.
  - Tỷ lệ đối chiếu chéo 3 phía đạt 100% khớp (0 mismatch trên toàn bộ dữ liệu).
- **Lý do loại bỏ UUID `id`**:
  - `id` chỉ là UUID cục bộ của lượt chạy, thay đổi qua các lần export lại và hoàn toàn không tồn tại trong `manifest.csv`. Dùng UUID `id` sẽ gây lệch khóa 100%.
- **Ý nghĩa**: Khóa nghiệp vụ ổn định giúp tránh join sai trận, tránh nhân đôi số lượt đấu và bảo đảm tính toàn vẹn cho mọi phân tích hạ nguồn.

---

## Slide 3 - Quyết định 2: Chiến lược De-duplicate qua thời gian & Chống Leakage

- **Thách thức từ dữ liệu crawl theo ngày**:
  - Khi hệ thống crawler chạy định kỳ, dataset công bố theo ngày khiến cùng một `episode_id` có thể xuất hiện lặp lại ở nhiều tệp manifest.
- **Thiết kế cơ chế De-duplicate trong Pipeline**:
  - Hợp nhất linh hoạt danh sách manifest từ nhiều đợt crawl khác nhau.
  - Gom nhóm theo `episode_id`: Giữ bản ghi có `create_time` mới nhất làm đại diện cho phân tích hạ nguồn.
  - Bổ sung các cột truy vết lịch sử: `first_seen_date`, `last_seen_date`, `manifest_occurrences`, `source_count`.
- **Kiểm soát Data Leakage**:
  - Phân tích theo chuỗi thời gian chỉ được phép dùng thông tin xuất hiện tại thời điểm crawl đó, không lấy thông tin tương lai gán ngược cho quá khứ.
  - Lưu vết 100% các bản ghi trùng lặp bị loại vào `duplicate_trace.csv` để đảm bảo tính minh bạch.

---

## Slide 4 - Quyết định 2 (tiếp): Đa phiên bản Engine & Đa cấu hình Game

- **Đa phiên bản Engine (Self-describing Specification)**:
  - Replay JSON tự mang schema trong khối `specification`.
  - Pipeline băm mã SHA-256 (`schema_signature_hash`) trên canonical JSON để kiểm soát toàn vẹn.
  - Tự động phát hiện trôi dạt schema (schema drift) giữa các version engine mà không cần hard-code bảng ánh xạ cột.
- **Đa cấu hình game (`configuration`)**:
  - *Câu hỏi:* Hai episode khác nhau về `startingMoney` (vốn ban đầu) hoặc `episodeSteps` (số bước chơi), so sánh trực tiếp điểm số có còn ý nghĩa không?
  - *Khẳng định:* **KHÔNG CÒN Ý NGHĨA.** Vốn ban đầu lớn hơn và số bước dài hơn tạo đòn bẩy tích lũy tài sản cơ học, không phản ánh năng lực thuật toán.
  - *Giải pháp chuẩn hóa:*
    1. Phân tầng (Stratified Comparison): Chỉ so sánh trong cùng nhóm cấu hình.
    2. Chuẩn hóa tỷ suất tích lũy ròng trên mỗi lượt (Normalized Score Rate):
       $$\text{Score Rate} = \frac{\text{sum\_score} - (\text{agent\_count} \times \text{startingMoney})}{\text{episodeSteps} \times \text{agent\_count}}$$

---

## Slide 5 - Quyết định 3: Bắt lỗi kép & Xử lý Structured Missingness

- **Rule-based vs Thống kê (Quantile 1% - 99%)**:
  - Rule-based kiểm tra quan hệ toán học ($|\text{sum} - \text{avg} \times 2| > 0.1$), tính toàn vẹn agent count, status count và số bước.
  - Kết quả đối chiếu: Thống kê bỏ sót các lỗi quan hệ điểm số vì điểm sai lệch vẫn nằm gần trung vị (median).
  - *Kết luận:* Rule-based là chốt chặn bắt buộc cho tính toàn vẹn (integrity); Thống kê chỉ đóng vai trò cảnh báo hành vi cực đoan, không được tự ý xóa dữ liệu.
- **Nguyên tắc bảo toàn ngữ nghĩa (Structured Missingness)**:
  - Tuyệt đối không impute `reward = null` thành `0`: Null là lỗi sập hệ thống (`ERROR`/`TIMEOUT`), 0 là kết quả thi đấu hợp lệ.
  - Bảo toàn hành vi chiến thuật `PASS` (`is_default_pass = True`).
  - Phân loại riêng các lệnh thị trường không có số lượng (`HIRE`, `BUY_LAND` mang `quantity_is_numeric = False`), không ép kiểu hay gán số lượng giả.
  - Với các episode chưa đồng bộ replay JSON trong lượt chạy mẫu, pipeline tự động gắn cờ `json_available = False`, giữ trong bảng audit cấp episode chứ không làm gián đoạn hệ thống.

---

## Slide 6 - Traceability và Tác động Downstream

- **Ma trận truy vết (`traceability.csv`)**:
  - Lưu vết chi tiết toàn bộ quyết định gắn cờ và phân loại theo từng cấp độ (`manifest` / `episode`).
  - Cam kết `reversible = True` trên 100% bản ghi: Dữ liệu gốc được bảo toàn nguyên vẹn, mọi quyết định đều có thể hoàn nguyên khi cần kiểm toán lại.
- **Đánh giá tác động Downstream (`ranking_impact.csv`)**:
  - Bài toán kiểm chứng: So sánh xếp hạng `avg_score` trước và sau làm sạch trên 300 episode mẫu có replay JSON đầy đủ.
  - Kết quả thực nghiệm: **0 thứ hạng bị thay đổi (`rank_changed = False` trên 100% bản ghi)**.
  - Ý nghĩa: Quy trình làm sạch loại bỏ dữ liệu bất thường mà không tạo ra bất kỳ sự xáo trộn hay thiên vị giả tạo nào trong bảng xếp hạng.

---

## Slide 7 - Kết luận và Sản phẩm bàn giao

- **Ba nguyên tắc bảo đảm của Pipeline**:
  1. **Đúng đắn:** Khóa join chuẩn xác, chống nhân đôi dữ liệu và chặn đứng Data Leakage.
  2. **Bảo toàn:** Giữ nguyên ngữ nghĩa dữ liệu gốc, tách bạch lỗi kỹ thuật với chiến thuật chơi.
  3. **Tái sử dụng:** Tự động phát hiện schema drift, sẵn sàng xử lý dữ liệu crawl ngày mới qua 1 dòng lệnh CLI.
- **Bộ sản phẩm bàn giao trong `Chapter_4/Bai_3/`**:
  * `pipeline.py`: Mã nguồn module hóa chuẩn PEP8, hỗ trợ CLI độc lập.
  * `Bai_3_pipeline.ipynb`: Notebook trình diễn trực quan kèm mã chạy kiểm chứng.
  * `data_quality_report.md`: Báo cáo chất lượng dữ liệu chi tiết đầy đủ bảng số liệu.
  * `output/`: Bộ dữ liệu sạch xuất khẩu kép (CSV & Parquet nén tối ưu).
