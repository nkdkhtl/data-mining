# Slide Outline - Bài 3: Pipeline làm sạch & hợp nhất đa nguồn (5-7 phút)

## Slide 1 - Bài toán và ba quyết định chính

- **Bối cảnh & Dữ liệu**:
  - Toàn bộ dữ liệu lấy từ nguồn duy nhất `Chapter_4/data` (manifest metadata và các replay JSON mô phỏng Kaggriculture).
  - Hợp nhất manifest đa nguồn theo ngày crawl với replay JSON: 667 episode, mẫu benchmark 300 replay, trích xuất 432.000 lượt đấu (turn) và 529.922 lệnh thị trường (market orders).
- **Mục tiêu**:
  - Không chỉ tạo bảng dữ liệu phẳng, mà phải bảo toàn danh tính trận đấu, ngăn chặn rò rỉ thông tin theo thời gian và phân loại chính xác bản chất bất thường.
- **Ba quyết định kiến trúc cốt lõi**:
  1. Hợp nhất bằng khóa nghiệp vụ `episode_id`, kiên quyết loại bỏ UUID `id`.
  2. Khử trùng lặp qua thời gian: Giữ bản ghi crawl mới nhất kèm theo vết lịch sử `first_seen_date` / `last_seen_date`.
  3. Tách biệt hoàn toàn lỗi kỹ thuật / thiếu nguồn khỏi ngoại lai thống kê; không biến missing thành zero và không can thiệp thô bạo vào hành vi thị trường.

---

## Slide 2 - Quyết định 1: Join bằng `episode_id` thay vì UUID `id`

- **Thực trạng dữ liệu**: Mỗi tệp JSON replay chứa hai mã định danh: `id` (chuỗi UUID nội bộ của lần export) và `info.EpisodeId` (số nguyên định danh trận đấu).
- **Chứng minh khóa chuẩn**:
  - `episode_id = filename stem = info.EpisodeId = manifest.episode_id`.
  - Tỷ lệ đối chiếu chéo 3 phía đạt 100% khớp (0 mismatch).
- **Lý do loại bỏ UUID `id`**:
  - `id` chỉ là UUID cục bộ của lượt chạy, thay đổi qua các lần crawl lại và hoàn toàn không tồn tại trong `manifest.csv`. Sử dụng `id` sẽ gây lệch khóa 100% khi join đa nguồn.
- **Ý nghĩa**: Khóa nghiệp vụ ổn định giúp loại bỏ triệt để nguy cơ join nhầm trận, nhân bản lượt đấu và làm sai lệch toàn bộ phân tích hạ nguồn.

---

## Slide 3 - Quyết định 2: Giữ bản mới nhất, lưu lịch sử crawl để tránh Leakage

- **Thách thức**: Khi crawl dữ liệu theo ngày, các episode xuất hiện lặp lại ở nhiều tệp manifest (1.334 dòng manifest thô từ 2 ngày crawl).
- **Giải pháp xử lý**:
  - Với các bản ghi trùng `episode_id`, chọn bản ghi có `create_time` mới nhất làm đại diện cho phân tích.
  - Bổ sung các cột truy vết lịch sử: `first_seen_date`, `last_seen_date`, `manifest_occurrences` (số lần xuất hiện), `source_count`.
- **Kiểm soát Data Leakage**:
  - Phân tích theo chuỗi thời gian chỉ được phép dùng thông tin xuất hiện tại thời điểm crawl đó, không được lấy thông tin của tương lai gán ngược cho quá khứ.
  - 100% bản ghi trùng lặp bị loại được ghi vào `duplicate_trace.csv` để đảm bảo tính minh bạch và khả năng tái lập.

---

## Slide 4 - Quyết định 3: Phân loại lỗi kỹ thuật và Outlier thống kê

- **Đa cấu hình game (`configuration`)**:
  - `startingMoney` và `episodeSteps` là các tham số môi trường hợp lệ, không phải lỗi.
  - So sánh trực tiếp điểm số giữa các trận khác vốn/bước là vô nghĩa. Pipeline đề xuất phân tầng (Stratification) hoặc chuẩn hóa theo tỷ suất tích lũy trên mỗi lượt (`normalized_score_per_step`).
- **Phân tách hai nhóm bất thường**:
  - *Lỗi kỹ thuật / Thiếu nguồn*: Mismatch quan hệ điểm ($|\text{sum} - \text{avg} \times 2| > 0.1$), thiếu file replay JSON (367 episode), lệch số bước hoặc số tác nhân.
  - *Outlier thống kê*: Giá trị nằm ngoài phân vị 1% - 99% của `avg_score`, `size_bytes` hoặc `actual_steps`.
- **Kết quả thực nghiệm**:
  - Có 24 outlier thống kê; 0 trường hợp chỉ bị cờ thống kê (`stat_only = 0`).
  - Rule-based là chốt chặn bắt buộc cho tính toàn vẹn (integrity); Thống kê chỉ đóng vai trò cảnh báo giá trị cực đoan, không được tự ý xóa dữ liệu.

---

## Slide 5 - Không biến missing thành zero, không xóa hành vi thị trường

- **Nguyên tắc bảo toàn ngữ nghĩa**:
  - Tuyệt đối không impute `reward = null` thành `0`: Null phản ánh lỗi kỹ thuật (`ERROR`/`TIMEOUT`), còn 0 là kết quả thi đấu thực tế.
  - Action mặc định `PASS` và reward `0` là các giá trị hợp lệ trong chiến thuật chơi Kaggriculture, được gắn cờ nhận diện chứ không xem là lỗi.
- **Bảo toàn lệnh thị trường (Market Orders)**:
  - Các thao tác như `HIRE` hay `BUY_LAND` không mang tham số số lượng được phân loại riêng qua `quantity_is_numeric = False`, không ép kiểu hoặc gán giá trị 0 giả tạo.
- **Shared Observation Mismatch**:
  - 216.000 cặp lượt đấu bị lệch 1 step ở trường `observation.step` do cơ chế ghi log tuần tự của game engine. Lỗi này được ghi nhận riêng vào `shared_checks.csv` mà không tự ý impute làm biến dạng dữ liệu gốc.

---

## Slide 6 - Traceability và Tác động Downstream

- **Ma trận truy vết (`traceability.csv`)**:
  - Toàn bộ 4.016 lượt gắn cờ và quyết định loại bỏ đều được lưu vết chi tiết theo từng cấp độ (`episode`/`manifest`), nêu rõ lý do và bảo đảm tính khả nghịch (`reversible = True`).
- **Xử lý thiếu nguồn**:
  - 367 episode thiếu replay JSON được lưu giữ trong bảng audit cấp episode, chỉ loại khỏi phân tích cấp lượt (turn-level).
- **Đánh giá tác động lên xếp hạng Downstream**:
  - So sánh thứ hạng `avg_score` trước và sau làm sạch trên 300 episode có replay JSON.
  - Kết quả: **0 thứ hạng bị thay đổi (`rank_changed = 0`)**.
  - Kết luận: Pipeline làm sạch loại bỏ dữ liệu hỏng mà không bóp méo hay thiên vị bất kỳ thực thể nào trong kết quả phân tích.

---

## Slide 7 - Kết luận và Sản phẩm bàn giao

- **Kiến trúc có thể tái sử dụng (Reusable Architecture)**:
  - Module `pipeline.py` hoàn chỉnh, độc lập hoàn toàn, hỗ trợ CLI, sẵn sàng áp dụng tự động cho các đợt crawl dữ liệu ngày mới.
- **Sản phẩm bàn giao**:
  1. Mã nguồn module hóa: `pipeline.py` (chuẩn PEP8, hỗ trợ tham số CLI).
  2. Bảng dữ liệu sạch & Audit: `output/` định dạng Parquet và CSV tốc độ cao.
  3. Báo cáo chất lượng dữ liệu: `data_quality_report.md` chi tiết và đầy đủ bảng biểu.
  4. Notebook trực quan: `Bai_3_pipeline.ipynb` minh họa trực quan từng bước xử lý.
  5. Slide báo cáo: `slides_outline.md` tóm tắt súc tích trong 5-7 phút.
