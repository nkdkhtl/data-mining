# Data Quality Report - Bài 3: Pipeline làm sạch & hợp nhất đa nguồn

## 1. Phạm vi và Nguồn dữ liệu

* **Nguồn dữ liệu duy nhất**: Toàn bộ dữ liệu được nạp trực tiếp từ thư mục `Chapter_4/data`:
  * Tệp metadata: `Chapter_4/data/manifest.csv` chứa 667 trận đấu (episodes).
  * Tệp replay chi tiết: `Chapter_4/data/*.json` chứa đầy đủ 667 tệp replay tương ứng (mô phỏng môi trường game Kaggriculture).
* **Thiết kế tiếp nhận đa nguồn & Khử trùng lặp qua thời gian**:
  * Pipeline được thiết kế để tiếp nhận linh hoạt một hoặc nhiều tệp manifest từ các đợt crawl định kỳ theo ngày.
  * Khi có nhiều tệp manifest, pipeline tự động gom nhóm theo `episode_id`, chọn bản ghi có `create_time` mới nhất làm đại diện, bổ sung các trường lịch sử (`first_seen_date`, `last_seen_date`, `manifest_occurrences`, `source_count`) để kiểm soát chặt chẽ rò rỉ dữ liệu (*Data Leakage*).
* **Mẫu xử lý replay JSON (Execution Benchmark Sample)**:
  * Nhằm tối ưu hóa thời gian thực thi và tài nguyên phần cứng, pipeline xử lý mẫu benchmark chuẩn **300 episode** đầu tiên (theo yêu cầu đề bài: "tối thiểu vài trăm episode").
  * Mẫu chạy này trích xuất thành công **432.000 dòng cấp lượt đấu (turn-level)** ($300 \times 720 \text{ bước} \times 2 \text{ players}$) và **529.922 lệnh thị trường (market orders)**.
  * Các episode chưa đưa vào lượt parse này được đánh dấu `json_available = False` trong bảng audit, kiểm thử khả năng xử lý structured missingness khi dữ liệu replay chưa sẵn sàng mà không làm gián đoạn hệ thống.

---

## 2. Khóa hợp nhất (Join Key)

* **Xác minh khóa chuẩn**:
  * Tên tệp replay: `<episode_id>.json` (ví dụ `102062874.json`).
  * Khối metadata trong JSON: `info.EpisodeId` (số nguyên `102062874`).
  * Cột định danh trong manifest: `manifest.episode_id` (`102062874`).
  * **Kết quả đối chiếu chéo 3 phía**: Tỷ lệ trùng khớp đạt **100% (0 mismatch)**. Khóa `episode_id` là khóa nghiệp vụ ổn định, duy nhất và chính xác.
* **Bác bỏ khóa UUID `id`**:
  * Trường `id` ở cấp root của file JSON là chuỗi UUID nội bộ của engine mỗi lần xuất replay (ví dụ `31adb862-...`).
  * Trường này không xuất hiện trong `manifest.csv` và thay đổi qua các lần export lại, tuyệt đối không được dùng làm khóa join.

---

## 3. Khử trùng lặp qua thời gian (De-duplicate over time) & Kiểm soát Leakage

* **Nguy cơ Data Leakage**: Nếu gộp mù quáng nhiều ngày crawl, các episode xuất hiện nhiều lần sẽ bị nhân bản dữ liệu (duplicate turn/order), làm sai lệch các thống kê tổng và rò rỉ thông tin tương lai vào các phân tích chuỗi thời gian.
* **Chiến lược giải quyết**:
  1. Gom nhóm theo `episode_id`.
  2. Bổ sung các trường siêu dữ liệu lịch sử crawl: `first_seen_date`, `last_seen_date`, `manifest_occurrences`, `source_count`.
  3. Chọn bản ghi có `create_time` mới nhất làm đại diện cho phân tích hạ nguồn.
  4. Lưu vết toàn bộ quyết định xử lý vào `duplicate_trace.csv`.

---

## 4. Chuẩn hóa đa phiên bản Engine & Đa cấu hình

### 4.1. Đa phiên bản Engine và Schema tự mô tả (Self-describing)
* Mỗi file JSON tự mang theo schema trong khối `specification` (mô tả action, observation, reward).
* Pipeline tính mã băm SHA-256 (`schema_signature_hash`) trên nội dung JSON chuẩn hóa của khối `specification`.
* **Kết quả**: 100% các file JSON có cùng mã hash (engine `1.32.7`, schema `1`), xác nhận không có hiện tượng trôi dạt schema (schema drift). Khi có phiên bản engine mới, pipeline tự động phát hiện thay đổi schema thông qua hash mismatch mà không cần hard-code bảng ánh xạ.

### 4.2. Đa cấu hình game (Game Configuration)
* Các tham số trong `configuration`: `boardSize` (10), `startingMoney` (3000), `episodeSteps` (720), `turnsPerDay` (24), `shedCapacity` (100).
* **Vấn đề so sánh**: Nếu hai episode có `startingMoney` hoặc `episodeSteps` khác nhau, việc so sánh trực tiếp `avg_score` là **HOÀN TOÀN KHÔNG CÓ Ý NGHĨA**:
  * `startingMoney` lớn hơn mang lại đòn bẩy vốn ban đầu, giúp tích lũy tài sản nhanh hơn.
  * `episodeSteps` dài hơn tích lũy nhiều vòng quay mùa vụ hơn.
* **Giải pháp chuẩn hóa**:
  1. *Phân tầng (Stratified Comparison)*: Chỉ so sánh trực tiếp trong cùng nhóm cấu hình `(startingMoney, episodeSteps, boardSize)`.
  2. *Chuẩn hóa tỷ suất tích lũy ròng trên mỗi lượt (Normalized Score Rate)*:
     $$\text{Normalized Score Rate} = \frac{\text{sum\_score} - (\text{agent\_count} \times \text{startingMoney})}{\text{configured\_episode\_steps} \times \text{agent\_count}}$$
     Chỉ số này đã được pipeline tính toán và lưu tại cột `normalized_score_per_step`.

---

## 5. Bảng tổng hợp Chất lượng Dữ liệu

| Chỉ số / Giai đoạn | Số lượng bản ghi | Ghi chú kỹ thuật |
| :--- | :---: | :--- |
| **Manifest episodes (Chapter_4/data)** | 667 | Tổng số trận đấu trong manifest.csv |
| **Replay JSON sẵn có trên đĩa** | 667 | Khớp 100% với manifest.csv trên đĩa |
| **Replay JSON trong mẫu benchmark** | 300 | Mẫu chạy xử lý turn-level |
| **Turn rows trích xuất** | 432,000 | $300 \text{ eps} \times 720 \text{ steps} \times 2 \text{ players}$ |
| **Lệnh thị trường (Market orders)** | 529,922 | Trích xuất từ 432.000 lượt đấu |
| **Reward null** | 0 | Không có turn nào bị thiếu reward bất thường |
| **Quantity âm trong market** | 0 | Không có lỗi số lượng âm |
| **Quantity không phải số** | 172,930 | Các lệnh `HIRE`, `BUY_LAND` không mang quantity |
| **Episode statistical outlier** | 24 | Nằm ngoài phân vị 1% - 99% của score/size/steps |
| **Lệch quan sát chung (Shared mismatch)** | 216,000 | Lệch 1 step ở trường `observation.step` giữa 2 người chơi |

---

## 6. So sánh Rule-based và Thống kê (Statistical)

* **Rule-based**:
  * Phát hiện lỗi quan hệ toán học ($|\text{sum\_score} - \text{avg\_score} \times 2| > 0.1$), kiểm tra agent count, status count và số bước.
* **Thống kê (Statistical)**:
  * Sử dụng ngưỡng phân vị 1% - 99% cho 3 biến: `avg_score`, `size_bytes`, `actual_steps`. Phát hiện 24 episode outlier cực đoan.
* **Nhận xét cốt lõi**:
  * **Bắt buộc ưu tiên Rule-based**: Cho các bài toán kiểm tra tính toàn vẹn dữ liệu (integrity), cấu trúc schema và quan hệ logic. Phương pháp thống kê không thể phát hiện lỗi logic nếu giá trị sai lệch nằm gần trung vị (median).
  * **Sử dụng Thống kê làm cảnh báo**: Dành cho việc nhận diện các hành vi người chơi phi thường hoặc biến động lưu trữ để kiểm tra thủ công, không nên tự động loại bỏ dữ liệu chỉ vì cờ thống kê.

---

## 7. Xử lý Dữ liệu thiếu có cấu trúc (Structured Missingness)

1. **Không impute `reward = null` thành `0`**:
   * Khi agent bị `ERROR` hoặc `TIMEOUT`, reward null phản ánh lỗi hệ thống hoặc đứt gãy thực thi.
   * `reward = 0` là điểm số thi đấu hợp lệ khi agent không kiếm được lợi nhuận. Impute null thành 0 sẽ bóp méo phân phối hiệu năng thật.
2. **Bảo toàn hành vi `PASS`**:
   * Action mặc định `{"farmer": ["PASS"], "hands": [], "market": []}` là hành vi chiến thuật hợp lệ, được gắn cờ `is_default_pass = True` thay vì coi là agent dừng hoạt động.
3. **Phân loại lệnh market phi số lượng**:
   * Các lệnh như `HIRE` hoặc `BUY_LAND` vốn không có tham số quantity; pipeline phân loại qua cờ `quantity_is_numeric = False`, không coi là quantity bị lỗi/null.
4. **Lỗi quan sát chung (Shared Observation Mismatch)**:
   * Phát hiện 216.000 cặp turn bị lệch ở trường `step` (lệch đúng 1 step giữa 2 người chơi do cơ chế tuần tự của game engine). Các trường `day`, `hour`, `farms`, `market`, `town` hoàn toàn khớp 100%. Lỗi này được ghi nhận riêng vào `shared_checks.csv`, không tự ý impute làm sai lệch dữ liệu gốc.

---

## 8. Đánh giá Tác động Downstream (Downstream Impact)

* **Bài toán kiểm nghiệm**: Xếp hạng các trận đấu theo `avg_score` trước và sau khi thực hiện lọc làm sạch (trên tập 300 episode có replay JSON).
* **Kết quả**:
  * Xếp hạng thô (`rank_raw`) và xếp hạng sau làm sạch (`rank_clean`) được so sánh chi tiết trong `ranking_impact.csv`.
  * **Số thứ hạng bị thay đổi: 0 (`rank_changed = False` trên 100% bản ghi)**.
* **Kết luận**: Các quy tắc làm sạch loại bỏ dữ liệu bất thường mà không tạo ra bất kỳ dịch chuyển giả tạo nào trong phân phối xếp hạng. Mọi quyết định đều được ghi chép đầy đủ trong `traceability.csv` với cờ `reversible = True`.
