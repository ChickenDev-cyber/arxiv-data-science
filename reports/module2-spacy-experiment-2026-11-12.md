# Thử nghiệm spaCy senter — 2026-11-12

Cấu hình: Python 3.12.13, spaCy 3.8.18, en_core_web_sm 3.8.0; CPU, chỉ pipe `senter`. Không NER/parser, không huấn luyện/tinh chỉnh. Cùng lớp bảo vệ LaTeX, vocab và điều kiện ranh giới như thử nghiệm Punkt.

## Kết quả

- 12 ca chẩn đoán: **10/12 đúng**. Đúng cả 4 ca initials (kể cả cặp tên mới R.T./Z.W.) và U.S. Department. Sai 2 ca kết thúc câu: `They worked in the U.S. Results followed.` và `We contacted K.D. Results followed.` đều bị gộp.
- Áp nguyên 40 kỳ vọng tokenizer cũ: **35 pass, 5 fail**. Các ca fail: Fig. 2, A vs. B, no. 2, literal placeholder sau câu và command lạ sau e. Đây là khác biệt phân đoạn, không mất ký tự.
- Cùng 1.574 đoạn của 11 bài TeX + 1 PDF-only: 16 đoạn thay đổi; số sentence 5.635 → 5.608. Không dùng số câu giảm để kết luận accuracy.
- Nội dung tokenizer không mất/thêm ký tự khi bỏ whitespace; node không phải sentence không đổi.
- Kiểm tra các đoạn thay đổi thật: sửa I.-M.I. ở 2506.20791, L.B./S.K. ở 2506.20794, K.D./J.L. ở 2506.20796 và U.S. Department ở 2506.20788. Vẫn gộp sai `compared to M. This highlights ...` ở 2506.20788.

## Kết luận

Cải thiện nhóm initials so với Punkt pretrained, nhưng chưa đạt kỳ vọng hiện tại và có lỗi gộp câu. Chưa thay tokenizer mặc định. Kết quả là hybrid spaCy + lớp bảo vệ LaTeX, không đại diện cho spaCy thuần hoặc accuracy toàn corpus.

## Tái hiện và artifact

```bash
python -m pip install spacy==3.8.18
python -m spacy download en_core_web_sm
PYTHONPATH=src python tools/evaluate_sentence_models.py spacy
PYTHONPATH=src python tools/evaluate_tokenizer_compatibility.py spacy
```

Phải dùng đúng en_core_web_sm 3.8.0 để tái hiện bản thử này. Dependency thử nghiệm ở `/tmp/arxiv-nlp-venv`, không sửa Python hệ thống.

Output: `data/module2-khang-review/spacy-experiment/`, gồm summary, output từng bài, evaluation.log, compatibility-tests.log và environment.txt. Baseline/Punkt vẫn được giữ nguyên. Các package có thể được thay đổi khi cài thêm SaT, nhưng environment.txt ghi lại môi trường lúc chạy spaCy.
