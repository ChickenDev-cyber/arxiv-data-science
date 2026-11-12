# Thử nghiệm Punkt cho Module 2 — 2026-11-12

## Cấu hình

- Adapter: `src/arxiv_pipeline/standardization/punkt_adapter.py`; NLTK **3.10.4**, mô hình pretrained `punkt_tab/english`. Không huấn luyện/tinh chỉnh trên 11 bài mẫu.
- Tokenizer mặc định vẫn là rule-based. Không đổi `process_prose` hay runner mặc định.
- Giữ bộ bảo vệ LaTeX và vocab bắt buộc hiện tại: mask cùng độ dài, dùng span của Punkt để cắt chuỗi gốc. Chỉ nhận ranh giới dấu chấm, giữ ngoặc đóng ở câu trước và không cắt trước hậu tố dính liền như ref.(ii). Không có luật hay vocab tên initials/U.S. bổ sung.
- Đây là thử nghiệm hybrid Punkt + bảo vệ LaTeX, không phải chất lượng Punkt thuần trên tiếng Anh. Việc mask command có thể ảnh hưởng ngữ cảnh mô hình.

## Test và đối chiếu

- Bộ test liên quan, baseline và 9 test adapter: **149 tests, 10 subtests pass**. Số này không có nghĩa mọi test tokenizer cũ đều đạt khi thay bằng Punkt.
- Khi chạy lại riêng 40 test tokenizer hiện tại với adapter Punkt thay thế: **38 pass, 2 fail**. Khác kỳ vọng ở `Wait... Last fragment` (gộp thay vì tách) và `Use e. \unknown{g.} Next.` (gộp thay vì tách sau e.). Không sửa kỳ vọng để làm test xanh.
- 12 ví dụ kiểm tra có expected output viết thủ công: baseline đúng **7/12**, Punkt **8/12**. Đây là tập chẩn đoán nhỏ, không phải ước lượng accuracy toàn corpus.

| Ca chẩn đoán | Baseline đúng | Punkt đúng |
|---|---|---|
| initials-hyphen | Không | Không |
| initials-list | Không | Không |
| initials-single | Không | Không |
| initials-unseen | Không | Không |
| country-continues | Không | Có |
| country-ends | Có | Có |
| initials-ends | Có | Có |
| decimal | Có | Có |
| math | Có | Có |
| citation | Có | Có |
| latex-spacing | Có | Có |
| url | Có | Có |

## Cùng 11 bài TeX và 1 PDF-only

- Chạy cùng **1574 đoạn prose**, không phát hiện mất/thêm ký tự tokenizer khi bỏ whitespace; node không phải sentence giữ nguyên.
- Số câu: baseline **5635**, Punkt **5630**. **4 đoạn** thay đổi phân đoạn; số câu tăng/giảm không phải thước đo đúng/sai.
- Đã đọc đối chiếu cả 4 đoạn thay đổi:
  - 2506.20788: nối đúng `U.S. Department of Energy`; nhưng gộp sai `compared to M. This highlights ...` (M là tên phương pháp và câu tiếp bắt đầu bằng This).
  - 2506.20793: gộp `...ÉQUIPE..."` với `Instructions:`; khác cách chia khối của baseline, chưa có gold để kết luận ngôn ngữ tổng quát.
  - 2506.20794: nối `n. 772293` và `A. Mucciarelli`; các initials L.B./S.K. vẫn bị tách.
- 2506.20791 và 2506.20796 output giống baseline: **I.-M.I., K.D., J.L. vẫn bị tách sai**. Punkt pretrained không giải quyết được nhóm lỗi chính đang nhắm tới.
- Thời gian tokenize local một lượt, không gồm load model, gather, cleanup hay ghi JSON: baseline **0.540s**, adapter Punkt **0.786s**. Chưa đo lặp hoặc peak RAM, không phải benchmark Colab.

## Kết luận

Chưa chuyển mặc định sang Punkt. Pretrained English cải thiện U.S. nhưng không xử lý được chuỗi initials nhiều chữ trong mẫu, đồng thời có trường hợp gộp sai mới. Không kết luận mọi giải pháp NLP đều kém hơn từ thử nghiệm này. Nếu tiếp tục, cần đánh giá sentence segmenter có ngữ cảnh khác hoặc Punkt được thích nghi trên corpus riêng với tập kiểm tra độc lập.

## Tái hiện

```bash
python -m pip install -r src/requirements-punkt.txt
python -m nltk.downloader -d "$PWD/data/nltk_data" punkt_tab
export NLTK_DATA="$PWD/data/nltk_data"
PYTHONPATH=src python tools/evaluate_punkt.py
PYTHONPATH=src python -m pytest -q tests/test_punkt_adapter.py
```

Trong phiên thử này dependency được cài riêng tại `/tmp/arxiv-punkt-deps` (không sửa Python hệ thống). Để dùng lại môi trường hiện có, đặt `PYTHONPATH=src:/tmp/arxiv-punkt-deps`.

Output đầy đủ từng đoạn và gold cases: `data/module2-khang-review/punkt-experiment/summary.json` và `<id>.json`. Nguồn/model/output thuộc data gitignored; không tải model tự động khi chạy parser. Các model tabular có thể được kiểm chứng bằng SHA-256 trong `model-checksums.json`.
