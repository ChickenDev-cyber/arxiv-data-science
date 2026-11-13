# Phương pháp tiếp theo sau Punkt

Ngày nghiên cứu: 2026-11-12. Chưa cài hoặc đánh giá các model dưới đây.

## Giữ kết quả Punkt

- Báo cáo: `reports/module2-punkt-experiment-2026-11-12.md`.
- Output và gold cases: `data/module2-khang-review/punkt-experiment/`.
- `implementation-snapshot/` chứa bản sao adapter, baseline tokenizer, utils, vocab, requirements, evaluator và tests tại thời điểm thử nghiệm.
- `experiment-manifest.json` lưu SHA-256 của các output và snapshot; `model-checksums.json` lưu hash các tệp model English đã dùng. Không bao gồm toàn bộ môi trường Python; đây không phải container môi trường có thể tái hiện bit-for-bit.
- Kết quả baseline và Punkt được giữ nguyên, tokenizer mặc định chưa đổi.

## Ứng viên

| Phương pháp | Đặc điểm có tài liệu xác nhận | Đánh giá áp dụng cho dự án |
|---|---|---|
| spaCy SentenceRecognizer (`senter`) | Thành phần thống kê chỉ dự đoán ranh giới câu; có thể bật từ pipeline được huấn luyện có sẵn | Đề xuất thử trước: chỉ cần sentence segmentation, không bật NER hay đầy đủ dependency parsing. Cần đo thực tế trên CPU và văn bản khoa học |
| Segment Any Text (`SaT`, `sat-3l-sm`) | Model chuyên phân đoạn; tác giả khuyến nghị biến thể `-sm` cho sentence segmentation tổng quát, model 3 lớp cho ứng dụng ưu tiên tốc độ; hỗ trợ ONNX CPU | Ứng viên tiếp theo nếu spaCy chưa đạt. Thêm runtime/model; chưa có số đo RAM hoặc chất lượng trên corpus của nhóm |
| Stanza (`tokenize`) | TokenizeProcessor kết hợp tokenization và sentence segmentation | Phương án dự phòng; cần đánh giá chi phí và offset, chưa có cơ sở khẳng định tốt hơn hai lựa chọn trên |

Nguồn chính thức:

- spaCy: https://spacy.io/usage/linguistic-features#sentence-segmentation và https://spacy.io/api/sentencerecognizer/
- SaT: https://github.com/segment-any-text/wtpsplit và https://aclanthology.org/2024.emnlp-main.665/
- Stanza: https://stanfordnlp.github.io/stanza/tokenize.html

Không dùng spaCy `sentencizer` thay cho `senter`: sentencizer là phương pháp theo luật, khác mục tiêu thử model có ngữ cảnh.

## Phạm vi thử nghiệm đề xuất

1. Thử spaCy `senter` bằng adapter tùy chọn, giữ nguyên API và baseline. Chưa huấn luyện riêng.
2. Tách bộ bảo vệ LaTeX/ánh xạ vị trí khỏi adapter Punkt để các model dùng cùng input và cùng điều kiện; không sửa output gốc để model dễ đạt test.
3. Giữ các bài cũ làm regression, bổ sung đoạn mới độc lập để tránh chỉ tối ưu theo 11 bài đã xem. 12 ca chẩn đoán cũ không đủ đại diện cho corpus.
4. Kiểm tra cả hai phía: initials giữa câu và cuối câu; U.S. Department và U.S. Results; tên phương pháp M. đứng cuối câu; math/citation; dấu ngoặc; viết tắt khoa học.
5. So ranh giới dự đoán với nhãn thủ công: precision/recall/F1 và ví dụ split/merge sai. Không dùng số câu toàn bài để kết luận accuracy.
6. Đo CPU, peak RAM, thời gian load riêng và thời gian tokenize theo batch. Model/data được cài trước, không tải mạng trong pipeline.
7. Chỉ thay mặc định nếu giảm lỗi trên tập độc lập và không làm hỏng bảo toàn LaTeX. Nếu senter chưa đạt thì mới thử SaT, tránh đưa nhiều dependency vào pipeline cùng lúc.

Các nhận định về thứ tự thử là đề xuất kỹ thuật cho dự án, không phải kết quả so sánh thực nghiệm giữa spaCy/SaT/Stanza.

## Cập nhật đã thực nghiệm — 2026-11-12

Các mục phía trên là kế hoạch trước khi chạy. Nay đã chạy spaCy rồi lưu kết quả, sau đó chạy SaT trên cùng 1.574 đoạn prose của 11 bài TeX và 1 PDF-only.

| Backend | Đúng / 12 ca chẩn đoán | Đạt / 40 kỳ vọng tokenizer cũ |
|---|---:|---:|
| Rule baseline | 7 | 40 |
| Punkt hybrid | 8 | 38 |
| spaCy senter hybrid | 10 | 35 |
| SaT sat-3l-sm ONNX hybrid | 9 | 27 |

12 ca chẩn đoán và 40 kỳ vọng cũ đo hai khía cạnh khác nhau; không gộp thành một accuracy hoặc xem là xếp hạng toàn corpus. Ca chẩn đoán đã được dùng nhiều lần trong quá trình phát triển, không phải held-out benchmark.

- spaCy xử lý được initials nhưng gộp sai U.S./K.D. ở cuối câu và M. tên phương pháp.
- SaT xử lý được initials, U.S. và ví dụ M. trong bài thật, nhưng gộp sai một số câu sau decimal/math. Mask x làm mất ngữ cảnh có thể là một nguyên nhân; chưa có ablation để quy lỗi cho model.
- Cả hai adapter giữ nguyên nội dung khi bỏ whitespace và giữ node không phải sentence. Baseline mặc định không đổi.
- Bộ test chính + adapter Punkt + ánh xạ dùng chung: **153 tests, 10 subtests pass**. Con số này không thay thế các lần kiểm tra compatibility có fail trong bảng.
- Tài liệu chi tiết: `module2-spacy-experiment-2026-11-12.md`, `module2-sat-experiment-2026-11-12.md` trong reports. Output, logs, môi trường và checksum tại thư mục data tương ứng.

Bước nghiên cứu hợp lý tiếp theo: đánh giá cách biểu diễn input cho model (giữ ngữ cảnh prose và ánh xạ offset, thay vì mask x dài) trên tập độc lập; chưa tự động đổi tokenizer mặc định hoặc tiếp tục thêm model chỉ dựa trên 12 ca nhỏ.
