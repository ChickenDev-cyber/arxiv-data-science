# So sánh Punkt, spaCy và SaT — Module 2 của Khang

Trạng thái hiện tại (2026-11-13): **spaCy senter mặc định** sau benchmark 100 ID. Quyết định SaT được giữ bên dưới như lịch sử thử nghiệm; không còn là backend mặc định.

Quyết định ban đầu (trước benchmark dải ID): chọn **SaT** theo quyết định của Khang; lựa chọn này không có nghĩa SaT thắng mọi test hoặc tốt nhất trên corpus của nhóm.

## Phạm vi và phương pháp

- Cùng **11 bài TeX** 2506.20788–2506.20798 (v1) thuộc dải của Khang, thêm 2506.20799v1 PDF-only; tổng **1.574 đoạn prose** sau các sửa cấu trúc.
- Cùng pipeline cleanup/math, bộ bảo vệ LaTeX, vocab và ánh xạ offset. Chỉ chấp nhận ranh giới dấu chấm ngoài vùng bảo vệ; xuất nội dung từ chuỗi gốc.
- **12 ca chẩn đoán** có expected output thủ công (initials giữa/cuối câu, U.S., decimal, math, cite, spacing LaTeX, URL). Không phải held-out dataset; đã được xem trong phát triển.
- **40 test baseline** đo tương thích các kỳ vọng tokenizer theo luật. Không gộp hai bộ đo thành một accuracy, không suy kết quả toàn corpus từ mẫu này.

## Kết quả tổng hợp

| Phương pháp | Cấu hình | Đúng / 12 ca chẩn đoán | Pass / 40 test cũ | Sentence trên mẫu | Đoạn khác baseline |
|---|---|---:|---:|---:|---:|
| Rule baseline | Python + regex/vocab | 7 | 40 | 5.635 | 0 |
| Punkt | NLTK 3.10.4, punkt_tab English | 8 | 38 | 5,630 | 4 |
| spaCy senter | spaCy 3.8.18, en_core_web_sm 3.8.0 | 10 | 35 | 5,608 | 16 |
| SaT | wtpsplit 2.2.2, sat-3l-sm ONNX CPU | 9 | 27 | 5,466 | 139 |

Các adapter không mất/thêm ký tự khi so chuỗi bỏ whitespace trên mẫu; node không phải sentence giữ nguyên. Đây là kiểm tra bảo toàn nội dung, không phải chứng minh phân đoạn đúng.

## Điểm mạnh và lỗi còn lại

| Nhóm ca | Punkt | spaCy | SaT |
|---|---|---|---|
| I.-M.I., K.D./J.L., L.B. giữa câu | Vẫn cắt sai | Sửa được | Sửa được |
| U.S. Department | Sửa được | Sửa được | Sửa được |
| U.S. cuối câu, sau đó Results | Đúng | Gộp sai | Đúng |
| K.D. cuối câu, sau đó Results | Đúng | Gộp sai | Gộp sai |
| M. tên phương pháp, sau đó This (bài thật) | Gộp sai | Gộp sai | Đúng |
| Một số câu sau decimal/math trong bộ chẩn đoán | Đúng | Đúng | Gộp sai |

## Hiệu năng và giới hạn đánh giá

- Một lượt gọi tuần tự từng đoạn sau khi load model: Punkt khoảng **0,79 s**, spaCy **3,37 s**, SaT **222,13 s**. Máy local CPU; SaT ONNX intra-op=2/inter-op=1, threshold mặc định 0.25.
- Đây là số đo chẩn đoán, không phải benchmark công bằng đầy đủ: môi trường Python/dependency khác nhau, chưa đo lặp, batch, peak RAM hoặc Colab; baseline từng lượt cũng có thời gian khác nhau. SaT chưa tận dụng batch.
- Mask dài bằng x có thể làm mất ngữ cảnh cho model. Kết quả là chất lượng **adapter hybrid trong dự án**, không phải kết luận về model thuần.
- Không chỉnh threshold/LoRA hoặc thêm initials vào vocab để tối ưu riêng cho mẫu.

## Quyết định SaT trước benchmark (lịch sử)

- **SaT từng được chọn làm mặc định trước khi benchmark dải ID**, dù spaCy đạt cao hơn trên 12 ca chẩn đoán và baseline đạt cao hơn trên 40 kỳ vọng cũ.
- Dùng `sat-3l-sm`, revision `137da054051ad9f1eac42025f758db4ac9f22535`; tokenizer XLM-R revision `e73636d4f797dec63c3081bb6ed5c7b0bb3f2089`; chạy CPU/offline sau bước setup.
- Không gọi model “SOTA trên corpus của nhóm”. SaT được giới thiệu là model hiện đại trong [repo tác giả](https://github.com/segment-any-text/wtpsplit); kết quả ở đây chưa chứng minh đạt SOTA.
- Giữ rule backend để regression/đối chiếu. Thiếu model/dependency phải báo lỗi, không tự chuyển thuật toán và không tải mạng trong parser.
- Biết trước còn lỗi gộp câu. Cần đánh giá trên tập độc lập và nghiên cứu cách biểu diễn input trước khi tuyên bố chất lượng tổng quát.

## Artifact và tái hiện

- Báo cáo chi tiết: [Punkt](module2-punkt-experiment-2026-11-12.md), [spaCy](module2-spacy-experiment-2026-11-12.md), [SaT](module2-sat-experiment-2026-11-12.md).
- Output local: `data/module2-khang-review/{punkt,spacy,sat}-experiment/`; mỗi thư mục có summary, từng bài, snapshot/checksum. Corpus/model/output thô không đưa lên GitHub.
- Công cụ: `tools/evaluate_punkt.py`, `tools/evaluate_sentence_models.py`, `tools/evaluate_tokenizer_compatibility.py`. Evaluator đọc dữ liệu review local; clone mới cần chuẩn bị corpus/review input, không có sẵn raw source trên GitHub.
- Hướng dẫn cài và API: `src/arxiv_pipeline/standardization/README.md`.

## Kiểm chứng bản chuẩn bị push — 2026-11-13

- Tạo nhánh riêng từ main mới nhất (29714bb), giữ tích hợp Module 4 của Mẫn Nhi.
- Regression với `ARXIV_SENTENCE_BACKEND=rule`: **183 tests pass, 2 skip, 10 subtests pass** (gồm parsing và bibtex trên main mới).
- Smoke test backend SaT thật, CPU/offline: **3 tests pass**.
- Chạy publication pipeline mặc định trên source thật 2506.20791v1: có 115 sentence, 13 formula, 9 figure; giữ đúng `I.-M.I. was supported ...`. Đây là phần tử sau dedup của một bài, không phải benchmark toàn corpus.
- 13/40 kỳ vọng legacy vẫn không đạt khi chọn SaT như đã ghi trong thử nghiệm; không đổi expected output hoặc tuyên bố tất cả compatibility test đã pass.
- Model/corpus không có trong commit. Cần setup model một lần theo README trước khi dùng backend mặc định.

## Quyết định mới sau benchmark dải ID — 2026-11-13

Khang chọn chuyển sang **spaCy senter** để đáp ứng quy mô 40.000+ bản trên CPU. Trên 93 bài TeX trong 100 ID: spaCy gọi từng đoạn mất 17,28 s và peak RSS 152 MiB; batch 32 trong harness mất 9,70 s và 164 MiB. SaT cấu hình thử 1/1 mất 1.283,91 s, peak RSS 1.310 MiB. Xem [benchmark dải ID](module2-range-benchmark-2026-11-13.md).

Code production chuyển backend mặc định sang spaCy nhưng vẫn gọi từng đoạn; chưa tích hợp batch 32. Ngoại suy tương ứng là khoảng **2,1 giờ tokenizer/40.000 bản TeX** tương tự mẫu, chưa gồm toàn pipeline và các version bổ sung. SaT giữ làm backend tùy chọn. Không sửa expected output để giấu 5/40 kỳ vọng legacy spaCy chưa đạt.

Kiểm chứng cập nhật spaCy mặc định: regression với rule **189 tests pass, 2 skip, 10 subtests pass**; smoke spaCy thật **3 tests pass**. Setup dùng model 3.8.0 đã cài và không tải lại khi đúng version.

Publication pipeline mặc định spaCy đã chạy trên 2506.20791v1: 114 sentence elements sau dedup, 13 formula, 9 figure; giữ đúng cụm I.-M.I. was supported. Backend SaT tùy chọn vẫn đạt 3 smoke tests sau khi thay mặc định.
