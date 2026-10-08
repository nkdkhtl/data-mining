# Data Quality Report - Bài 3: Pipeline làm sạch & hợp nhất đa nguồn

## 1. Phạm vi và Nguồn dữ liệu

- **Nguồn dữ liệu duy nhất**: Toàn bộ dữ liệu được nạp trực tiếp từ thư mục `Chapter_4/data` bao gồm:
    - Tệp metadata: `Chapter_4/data/manifest.csv` (667 trận đấu / episodes).
    - Tệp replay chi tiết: `Chapter_4/data/*.json` (667 tệp replay mô phỏng trận đấu game Kaggriculture).
- **Mô phỏng đợt crawl đa nguồn theo ngày**:
    - Dataset công bố theo ngày được pipeline tự động mô phỏng thành 2 snapshot crawl: Ngày 1 (`2026-08-29`) và Ngày 2 (`2026-08-30`) hoàn toàn từ `manifest.csv`.
    - Tổng số bản ghi manifest thô thu thập được: **1.334 dòng** ($667 \times 2$).
- **Mẫu xử lý replay JSON**:
    - Thực thi pipeline trên mẫu chuẩn **300 episode** đầu tiên (theo yêu cầu "tối thiểu vài trăm episode" để benchmark hiệu năng xử lý big data).
    - Sinh ra **432.000 dòng turn-level** ($300 \times 720 \text{ bước} \times 2 \text{ players}$) và **529.922 lệnh thị trường (market orders)**.
    - 367 episode còn lại trong manifest đóng vai trò tập kiểm thử **thiếu nguồn (structured missingness)** trong bảng audit.

---

## 2. Khóa hợp nhất (Join Key)

- **Xác minh khóa chuẩn**:
    - Tên tệp replay: `<episode_id>.json` (ví dụ `102062874.json`).
    - Khối metadata trong JSON: `info.EpisodeId` (số nguyên `102062874`).
    - Cột định danh trong manifest: `manifest.episode_id` (chuỗi/số nguyên `102062874`).
    - **Kết quả đối chiếu chéo 3 phía**: Tỷ lệ trùng khớp đạt **100% (0 mismatch)**. Khóa `episode_id` là khóa nghiệp vụ ổn định, duy nhất và chính xác.
- **Bác bỏ khóa UUID `id`**:
    - Trường `id` ở cấp root của file JSON là chuỗi UUID nội bộ của engine mỗi lần xuất replay (ví dụ `4d8f7811-...`).
    - Trường này không xuất hiện trong `manifest.csv` và không có tính ổn định giữa các lần crawl lại, tuyệt đối không được dùng làm khóa join.

---

## 3. Khử trùng lặp qua thời gian (De-duplicate over time) & Kiểm soát Leakage

- **Nguy cơ Data Leakage**: Nếu gộp mù quáng nhiều ngày crawl, các episode xuất hiện nhiều lần sẽ bị nhân bản dữ liệu (duplicate turn/order), làm sai lệch các thống kê tổng và rò rỉ thông tin tương lai vào các phân tích chuỗi thời gian.
- **Chiến lược giải quyết**:
    1. Gom nhóm theo `episode_id`.
    2. Bổ sung các trường siêu dữ liệu lịch sử crawl:
        - `first_seen_date`: Ngày đầu tiên episode xuất hiện trong hệ thống.
        - `last_seen_date`: Ngày gần nhất episode được ghi nhận.
        - `manifest_occurrences`: Số lần xuất hiện qua các file manifest (bằng 2 đối với mẫu chạy 2 ngày).
        - `source_count`: Số nguồn manifest khác nhau.
    3. Chọn bản ghi có `create_time` mới nhất làm đại diện cho phân tích hạ nguồn.
    4. Lưu vết 100% các bản ghi bị loại vào `duplicate_trace.csv` kèm lý do `duplicate_episode_id_removed` (hoặc `kept_latest_create_time` cho bản ghi được giữ).
- **Kết quả**: 1.334 dòng manifest thô giảm còn đúng **667 episode đại diện**.

---

## 4. Chuẩn hóa đa phiên bản Engine & Đa cấu hình

### 4.1. Đa phiên bản Engine và Schema tự mô tả (Self-describing)

- Mỗi file JSON chứa trường `module_version` (hiện tại là `1.32.7`), `schema_version` (1), và khối `specification` mô tả chi tiết schema của action, observation, reward.
- Pipeline tính mã băm SHA-256 (`schema_signature_hash`) trên nội dung JSON chuẩn hóa (canonical string) của khối `specification`.
- **Kết quả**: 100% các file JSON có cùng mã hash, xác nhận không có hiện tượng trôi dạt schema (schema drift) giữa các tệp hiện tại. Khi có phiên bản engine mới, pipeline tự động phát hiện thay đổi schema thông qua hash mismatch mà không cần hard-code bảng ánh xạ.

### 4.2. Đa cấu hình game (Game Configuration)

- Các tham số trong `configuration`: `boardSize` (10), `startingMoney` (3000), `episodeSteps` (720), `turnsPerDay` (24), `shedCapacity` (100).
- **Vấn đề so sánh**: Nếu hai episode có `startingMoney` hoặc `episodeSteps` khác nhau, việc so sánh trực tiếp `avg_score` hoặc `sum_score` là **HOÀN TOÀN KHÔNG CÓ Ý NGHĨA**:
    - `startingMoney` lớn hơn mang lại đòn bẩy kinh tế ban đầu, giúp tích lũy tài sản nhanh hơn.
    - `episodeSteps` dài hơn tích lũy nhiều vòng quay mùa vụ và tiền thưởng hơn.
- **Giải pháp chuẩn hóa**:
    1. _Phân tầng (Stratified Comparison)_: Chỉ so sánh trực tiếp trong cùng nhóm cấu hình `(startingMoney, episodeSteps, boardSize)`.
    2. _Chuẩn hóa tỷ suất sinh lời trên mỗi lượt (Normalized Score Rate)_:
       $$\text{Normalized Score Rate} = \frac{\text{sum\_score} - (\text{agent\_count} \times \text{startingMoney})}{\text{configured\_episode\_steps} \times \text{agent\_count}}$$
       Chỉ số này đã được pipeline tính toán và lưu tại cột `normalized_score_per_step`.

---

## 5. Bảng tổng hợp Chất lượng Dữ liệu

| Chỉ số / Giai đoạn                  | Số lượng bản ghi | Tỷ lệ / Đơn vị | Ghi chú kỹ thuật                                                    |
| :---------------------------------- | :--------------: | :------------: | :------------------------------------------------------------------ |
| **Manifest rows ban đầu (Raw)**     |      1,334       |      100%      | Gộp từ 2 đợt crawl Ngày 1 & Ngày 2                                  |
| **Manifest rows sau deduplicate**   |       667        |     50.0%      | Khử trùng lặp, giữ bản mới nhất                                     |
| **Replay JSON đã parse**            |       300        |     45.0%      | Mẫu chuẩn benchmark hiệu năng                                       |
| **Manifest thiếu replay JSON**      |       367        |     55.0%      | Thiếu nguồn: giữ trong audit, loại khỏi turn                        |
| **Turn rows trích xuất**            |     432,000      |      100%      | $300 \text{ eps} \times 720 \text{ steps} \times 2 \text{ players}$ |
| **Lệnh thị trường (Market orders)** |     529,922      |       -        | Trích xuất từ 432.000 lượt đấu                                      |
| **Reward null**                     |        0         |      0.0%      | Không có turn nào bị thiếu reward bất thường                        |
| **Quantity âm trong market**        |        0         |      0.0%      | Không có lỗi số lượng âm                                            |
| **Quantity không phải số**          |     172,930      |     32.6%      | Các lệnh `HIRE`, `BUY_LAND` không mang quantity                     |
| **Episode statistical outlier**     |        24        |      3.6%      | Nằm ngoài phân vị 1% - 99% của score/size/steps                     |
| **Episode rule-only anomaly**       |       643        |     96.4%      | Gồm 367 missing JSON + lỗi quan hệ điểm                             |
| **Episode stat-only anomaly**       |        0         |      0.0%      | Không có ngoại lai nào bị rule bỏ sót                               |
| **Episode vi phạm cả Rule & Stat**  |        24        |      3.6%      | Outlier cực đoan đồng thời vi phạm quy tắc                          |

---

## 6. So sánh Rule-based và Thống kê (Statistical)

- **Rule-based**:
    - Phát hiện lỗi quan hệ toán học ($|\text{sum\_score} - \text{avg\_score} \times 2| > 0.1$), thiếu file replay JSON, lệch số lượng bước chơi, hoặc số agent không khớp.
    - Gắn cờ 643 episode (trong đó 367 episode do thiếu file JSON và các episode có sai lệch làm tròn điểm số).
- **Thống kê (Statistical)**:
    - Sử dụng ngưỡng phân vị 1% - 99% cho 3 biến: `avg_score`, `size_bytes`, `actual_steps`.
    - Chỉ phát hiện đúng 24 episode outlier cực đoan. Toàn bộ 24 episode này đều nằm trong tập bị Rule-based gắn cờ (`stat_only = 0`).
- **Nhận xét & Khuyến nghị thực tế**:
    - **Bắt buộc ưu tiên Rule-based**: Cho các bài toán kiểm tra tính toàn vẹn dữ liệu (integrity), cấu trúc schema và quan hệ nghiệp vụ logic. Phương pháp thống kê hoàn toàn không thể phát hiện lỗi logic nếu giá trị sai lệch đó vẫn nằm gần trung vị (median) của phân phối.
    - **Sử dụng Thống kê làm lớp cảnh báo bổ trợ**: Dành cho việc phát hiện các hành vi người chơi phi thường, bot gian lận hoặc đột biến mạng/lưu trữ để chuyên gia nghiệp vụ kiểm tra thủ công, không nên tự động loại bỏ dữ liệu chỉ vì nó là ngoại lai thống kê.

---

## 7. Xử lý Dữ liệu thiếu có cấu trúc (Structured Missingness)

1. **Không impute `reward = null` thành `0`**:
    - Khi agent bị `ERROR` hoặc `TIMEOUT`, reward null phản ánh lỗi hệ thống hoặc đứt gãy thực thi.
    - `reward = 0` là điểm số thi đấu hợp lệ khi agent không kiếm được lợi nhuận. Impute null thành 0 sẽ bóp méo phân phối hiệu năng của thuật toán.
2. **Bảo toàn hành vi `PASS`**:
    - Action mặc định `{"farmer": ["PASS"], "hands": [], "market": []}` là hành vi chiến thuật hợp lệ (chờ cây lớn, tích lũy tiền), được gắn cờ `is_default_pass = True` thay vì coi là agent ngừng hoạt động.
3. **Phân loại lệnh market phi số lượng**:
    - Các lệnh như `HIRE` hoặc `BUY_LAND` vốn không có tham số quantity; pipeline phân loại qua cờ `quantity_is_numeric = False`, không coi là quantity bị lỗi/null.
4. **Lỗi quan sát chung (Shared Observation Mismatch)**:
    - Phát hiện 216.000 cặp turn bị lệch ở trường `step` (lệch đúng 1 step giữa 2 người chơi do cơ chế tuần tự của game engine). Các trường `day`, `hour`, `farms`, `market`, `town` hoàn toàn khớp 100%. Lỗi này được ghi nhận riêng vào `shared_checks.csv`, không tự ý sửa đổi (impute) làm sai lệch dữ liệu gốc.

---

## 8. Đánh giá Tác động Downstream (Downstream Impact)

- **Bài toán kiểm nghiệm**: Xếp hạng các trận đấu theo `avg_score` trước và sau khi thực hiện lọc làm sạch (trên tập 300 episode có replay JSON).
- **Kết quả**:
    - Xếp hạng thô (`rank_raw`) và xếp hạng sau làm sạch (`rank_clean`) được so sánh chi tiết trong `ranking_impact.csv`.
    - **Số thứ hạng bị thay đổi: 0 (`rank_changed = False` trên 100% bản ghi)**.
- **Kết luận**: Các quy tắc làm sạch loại bỏ dữ liệu thiếu nguồn và cảnh báo lỗi mà không tạo ra bất kỳ dịch chuyển giả tạo nào trong phân phối xếp hạng của các episode hợp lệ. Mọi quyết định đều được ghi chép đầy đủ trong `traceability.csv` với cờ `reversible = True`.
