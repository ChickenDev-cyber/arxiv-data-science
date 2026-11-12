# Thử nghiệm SaT — 2026-11-12

## Cấu hình

- Python 3.12.13; wtpsplit 2.2.2; ONNX Runtime 1.31.1; model `sat-3l-sm`, revision `137da054051ad9f1eac42025f758db4ac9f22535`.
- ONNX CPUExecutionProvider: intra_op=2, inter_op=1. Threshold mặc định 0.25, không LoRA, không tinh chỉnh. Model ONNX khoảng 408 MiB; không phải số đo peak RAM.
- Tokenizer facebookAI/xlm-roberta-base tải sẵn ở data/models. Chạy với HF_HUB_OFFLINE=1 và TRANSFORMERS_OFFLINE=1; không tải mạng trong pipeline.
- Cùng bộ bảo vệ LaTeX, vocab, mask cùng độ dài và lọc ranh giới chỉ dấu chấm như Punkt/spaCy. Đây là đánh giá adapter hybrid, không phải benchmark SaT thuần.

## Kết quả

- 12 ca chẩn đoán: **9/12 đúng**. Đúng cả 4 ca initials, U.S. Department và U.S. ở cuối câu. Gộp sai ở K.D. cuối câu, `Accuracy is 3.14. Results follow.`, và `Use $x = 3.14$. Next.`.
- Áp nguyên 40 kỳ vọng tokenizer cũ: **27 pass, 13 fail**. Log ghi đầy đủ. Nhiều ca sai là gộp câu sau số, math hoặc vùng kỹ thuật; không được diễn giải đây là 13 lỗi mất nội dung.
- Cùng 1574 đoạn prose của 11 bài TeX + 1 PDF-only: **139 đoạn** khác baseline; sentence **5.635 → 5,466**.
- Kiểm tra bảo toàn ký tự khi bỏ whitespace đạt trên mọi đoạn; node không phải sentence giữ nguyên. Điều này không chứng minh ranh giới câu đúng.
- Đọc đối chiếu mẫu thay đổi: sửa I.-M.I. (2506.20791), K.D./J.L. (2506.20796), U.S. Department và giữ đúng hai câu `compared to M. This highlights ...` (2506.20788). Tuy nhiên gộp hai câu liên tiếp bắt đầu bằng citet ở 2506.20788 và gộp qua par ở 2506.20794.
- Thời gian gọi adapter tuần tự từng đoạn sau load model: **222.134s**; baseline cùng lượt **0.797s**. Không benchmark batch, không đo peak RAM hay Colab. Không coi số đo này là tốc độ tốt nhất của SaT: tác giả khuyến nghị batch thay vì gọi từng đoạn.

## Kết luận

Chưa thay tokenizer mặc định bằng SaT. Có cải thiện thật ở initials nhưng còn gộp sai câu đơn giản trong adapter hiện tại. Mask toàn bộ vùng kỹ thuật bằng x có thể làm mất ngữ cảnh; chưa có thí nghiệm ablation để tách lỗi model khỏi lỗi biểu diễn. Không kết luận SaT nói chung kém hơn spaCy hoặc rule-based trên corpus khoa học.

## Tái hiện

```bash
python -m pip install -r src/requirements-segmentation.txt
export ARXIV_SAT_MODEL="$PWD/data/models/sat-3l-sm"
export ARXIV_SAT_TOKENIZER="$PWD/data/models/xlm-roberta-base-tokenizer"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
PYTHONPATH=src python tools/evaluate_sentence_models.py sat
PYTHONPATH=src python tools/evaluate_tokenizer_compatibility.py sat
```

Model/tokenizer phải được tải trước. Output và provenance tại `data/module2-khang-review/sat-experiment/`: summary, từng bài, logs, environment, model-checksums, implementation-snapshot và experiment-manifest. Không ghi đè kết quả Punkt/spaCy.

Nguồn thiết lập: https://github.com/segment-any-text/wtpsplit (model -sm, ONNX CPU, batch).
