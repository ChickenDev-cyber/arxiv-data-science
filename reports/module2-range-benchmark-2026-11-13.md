# Benchmark spaCy và SaT theo dải Khang — 2026-11-13

## Mẫu và môi trường

- 100 ID cố định: 70 ID trải đều từ 2506.20788 đến 2506.24000, 30 ID từ 2507.00001 đến 2507.01658; tất cả v1, thuộc dải Khang. Mẫu systematic theo ID, không phải mẫu ngẫu nhiên toàn bộ 40.000 bài.
- Kết quả: **93 bài TeX có prose**, **6 PDF-only**, **1 nguồn tải không hoàn tất** (2506.21859, hai lượt timeout). 2507.01544 đã tải được khi retry; không thay ID lỗi bằng ID khác.
- Tổng **7,525 đoạn prose**, **4,803,251 ký tự**. Hai nguồn có missing-include warning: 2506.22091 (xy) và 2507.00230 (ref.bbl); không khẳng định cây của mọi nguồn đều đúng.
- CPU Intel Core i5-8365U @ 1.60 GHz, 4 core/8 logical CPU; Python 3.12.13. SaT sat-3l-sm ONNX CPU, intra-op=2/inter-op=1; spaCy en_core_web_sm 3.8.0 chỉ chạy senter. Model và dependency giống các thử nghiệm trước.
- Máy local đang sử dụng, không phải môi trường CPU cô lập hoặc benchmark Colab nộp bài. Một phần download/preparation chạy nền trong các phần SaT đầu; không chạy hai model NLP đồng thời.

## Kết quả trên cùng workload

Đo tokenizer sau cleanup/normalize: bao gồm mask LaTeX, model, ánh xạ về chuỗi gốc, kiểm tra bảo toàn và hash output. Không gồm tải mạng/model, load/warmup model, gather/cleanup/collect hierarchy, references, dedup hay lưu DB.

| Cấu hình | Thời gian mẫu | Giây/bản có TeX | Peak RSS process | Ngoại suy 40.000 bản có TeX |
|---|---:|---:|---:|---:|
| spaCy batch 1 | 17.28 s | 0.186 | 152 MiB | 2.06 giờ |
| spaCy batch 32 | 9.70 s | 0.104 | 164 MiB | 1.16 giờ |
| SaT outer 1 / internal 1 | 1283.91 s | 13.805 | 1310 MiB | 153.39 giờ |

- spaCy batch 1 và 32 có output hash giống nhau trên toàn bộ mẫu: **True**. Kiểm tra ký tự khi bỏ whitespace đạt; không thay thế đánh giá ranh giới câu bằng gold labels.
- SaT chạy bốn phần không trùng bài để chồng lấp thời gian chuẩn bị/download. Đã kiểm tra union workload khớp chính xác mẫu cuối, không bỏ hoặc tính hai lần các đoạn. Cộng thời gian inference, lấy max peak RSS qua các process; không cộng RAM và không tính các lần load lặp vào ngoại suy.
- Chuẩn bị corpus (extraction, gather, cleanup/math và quét cấu trúc để lấy prose, chưa tạo sentence node) mất **35.89 s** trong lượt cuối. Đây chưa phải toàn bộ pipeline.

## Pilot batch SaT và cấu hình production

Outer batch là số đoạn truyền mỗi lượt; internal batch là số cửa sổ SaT xử lý cùng lúc. SaT dùng block_size=512, stride=64, threshold mặc định 0.25.

Pilot gồm 3 bài cũ 2506.20788, 2506.20793, 2506.20798; 236 đoạn/144.345 ký tự, xếp theo độ dài. Một lượt mỗi cấu hình, chưa đo lặp để kết luận thống kê.

| Outer batch | Internal window batch | Thời gian | Peak RSS |
|---:|---:|---:|---:|
| 1 | 1 | 29.53 s | 1306 MiB |
| 1 | 32 | 33.15 s | 1735 MiB |
| 8 | 8 | 32.48 s | 1344 MiB |
| 32 | 32 | 37.34 s | 2271 MiB |

- Tất cả cấu hình pilot có output hash giống nhau: **True**.
- Các cấu hình 1/1, 8/8, 32/32 thay cả outer batch và batch cửa sổ nội bộ; không suy ra ảnh hưởng riêng của mỗi biến. Có thêm đối chứng outer=1/internal=32, tương ứng cách gọi production hiện tại.
- Benchmark mẫu lớn dùng outer=1/internal=1 vì đây là mốc nhanh nhất trong pilot lúc chọn cấu hình. **Tại thời điểm benchmark, backend production là SaT gọi từng đoạn với internal batch mặc định 32. Sau benchmark, mặc định đã đổi sang spaCy; SaT vẫn giữ cấu hình đó khi được chọn rõ ràng.** Bảng mẫu lớn không phải thời gian đo trực tiếp cấu hình production trên 93 bài.

## Diễn giải

- Chỉ ngoại suy nếu độ dài và cấu trúc prose tương tự mẫu. Không phải dự báo chắc chắn; chưa có confidence interval hoặc nhiều lượt đo.
- 40.000 là số **bản có TeX** trong bảng. Nếu là publication có nhiều version, chi phí tăng theo tổng văn bản mọi version; PDF-only hầu như không có chi phí NLP.
- SaT trên CPU này vẫn chậm hơn đáng kể; batching không tự động tăng tốc trong pilot và batch lớn tăng RAM. spaCy khả thi hơn về thời gian CPU trong các cấu hình đã đo, nhưng các giới hạn chất lượng của thử nghiệm trước vẫn còn.
- spaCy batch 32 cũng là cấu hình của harness, chưa được nối thành batch trong workflow production. Chọn backend spaCy trong code hiện tại vẫn gọi từng đoạn.
- Trong lượt benchmark chưa đổi backend. Sau đó Khang đã chọn spaCy làm mặc định; batch 32 của spaCy vẫn chưa tích hợp vào production. Chưa benchmark GPU, quantization, multiprocessing hoặc toàn bộ pipeline M1–M5.

## Tái hiện và artifact

```bash
# Cần environment/model local theo README Module 2.
PYTHONPATH=src python tools/benchmark_m2_range.py download
PYTHONPATH=src python tools/benchmark_m2_range.py prepare
PYTHONPATH=src HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python tools/benchmark_m2_range.py run --backend spacy --batch 1
PYTHONPATH=src HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python tools/benchmark_m2_range.py run --backend spacy --batch 32
PYTHONPATH=src HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python tools/benchmark_m2_range.py run --backend sat --batch 1
```

- Lệnh download yêu cầu mạng, cách request 3 giây, tối đa 64 MiB/archive và timeout 60 s; lượt benchmark đã retry lỗi một lần. Manifest lượt đầu và retry được giữ riêng.
- Data/JSON/logs/snapshot: `data/module2-range-benchmark-2026-11-13/`; pilot: `data/module2-batch-pilot-2026-11-13/`.
- `summary.json`, `preparation.json`, `downloads-first-pass.json`, `downloads.json`, `metadata.json`, `environment.json` và các output từng cấu hình lưu đủ phạm vi và số đo. Corpus/model/raw output không commit Git.
- Test harness/extraction: `tests/test_range_benchmark.py` (3 tests pass).
