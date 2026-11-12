# Nghiệm thu mẫu Module 2 — Khang

Ngày: 2026-11-12 (Asia/Bangkok). Đây là đánh giá phát triển, chưa phải báo cáo benchmark Colab hay chứng nhận độ chính xác toàn corpus.

## Phạm vi và nguồn dữ liệu

- Dải xác nhận từ `src/arxiv_pipeline/config.py`: **2506.20788 → 2507.01658**, bao gồm hai đầu.
- Tải tuần tự 12 bản **v1** từ `https://export.arxiv.org/src/<id>v1`, nghỉ 3 giây giữa request; chỉ trích xuất file thường `.tex` và `.bib`, kiểm tra đường dẫn và kích thước. Không thực thi TeX.
- **11 bài có TeX**, 1 bản PDF-only. Đây là các ID đầu dải, không phải mẫu ngẫu nhiên hay toàn bộ phiên bản.
- Chạy `SourceGatherer.inspect` → `build_hierarchy` với callback ghi lại `clean_text` → `normalize_math` → `process_prose`. Không chạy Module 4/reference dedup, Module 5/storage hoặc benchmark toàn pipeline.
- Mỗi bài có `data/module2-khang-review/<id>/review.json`: URL, SHA-256 archive, nguồn merged, từng đoạn raw/cleaned/normalized/sentences, cây và diagnostics gathering. Dữ liệu nằm trong thư mục gitignored, không commit corpus.
- Đọc thủ công mẫu mở đầu, citation/viết tắt, đoạn dài nhất và các fragment đáng ngờ của từng bài có TeX; không đọc và gán nhãn toàn bộ mọi câu.

## Kết quả chạy

| arXiv v1 | Trạng thái | Sentence | Formula | Figure/table | Nhận xét kiểm tra mẫu |
|---|---|---:|---:|---:|---|
| 2506.20788 | ok | 520 | 34 | 10 | Prose có i.e. và inline math được giữ; algorithm và subequations còn lọt vào sentence. |
| 2506.20789 | ok | 784 | 305 | 1 | Citation có optional arguments được giữ; end{proof}/end{remark} và tùy chọn list còn lọt vào sentence. |
| 2506.20790 | ok | 368 | 14 | 16 | Bỏ được textbf trong prose; algorithm vẫn thành prose, còn setcounter và raggedright environment. |
| 2506.20791 | ok | 116 | 13 | 9 | Decimal/citation được giữ; initials I.-M.I. bị tách khỏi phần was supported; định dạng cũ {\it ...} chưa gỡ. |
| 2506.20792 | ok | 1331 | 210 | 4 | Công thức block được nhận diện; còn markup theorem/proof và các khối ytableau/scalebox trong prose. |
| 2506.20793 | ok | 152 | 0 | 23 | e.g. đúng nhưng eg. bị tách; textbf trong caption còn nguyên; hierarchy cắt nhầm tại item trong itemsep. |
| 2506.20794 | ok | 222 | 10 | 23 | e.g./citation giữ đúng; abstract command nhiều câu bị giữ thành một sentence; initials L.B. bị tách. |
| 2506.20795 | ok | 191 | 0 | 14 | e.\,g. bị tách sai; định dạng bên trong command bọc bảng còn nguyên. |
| 2506.20796 | ok | 154 | 4 | 5 | Citation được giữ; affiliation lọt vào prose và initials K.D./J.L. bị tách. |
| 2506.20797 | ok | 1582 | 60 | 0 | Inline \(…\) được chuẩn hóa; macro tùy biến được giữ, còn address/tableofcontents/theorem markup. |
| 2506.20798 | ok | 263 | 52 | 11 | Bỏ textbf trong prose và giữ math; comment environment chưa được loại trừ. |
| 2506.20799 | empty_source | 0 | 0 | 0 | Endpoint source trả PDF: ghi nhận empty_source, không tính vào 11 bài TeX. |

- Tổng: **5.683 sentence**, **702 formula**, **116 figure/table**; đây là số node parser tạo ra, không phải số phần tử đã được xác nhận đúng.
- **1.582 đoạn prose**: không phát hiện cleanup hoặc normalize math thay đổi sau lần chạy thứ hai.
- Trên 1.582 đoạn, nối output sentences và bỏ mọi whitespace cho kết quả bằng normalized input đã bỏ whitespace. Kiểm tra này chỉ chứng minh tokenizer không mất/thêm ký tự ngoài whitespace, không chứng minh ranh giới câu hay cleanup đúng nghĩa.
- Gathering không báo missing include trong mẫu. Cả 11 bài đều có compilation_path dài 1; mẫu chưa xác minh multi-file.
- Tổng thời gian build hierarchy kèm ghi diagnostics trong RAM: **4.6827s** trên máy local. Không gồm download, extraction, gather, ghi JSON; không coi đây là benchmark chính thức.

## Lỗi và giới hạn đã xác nhận

### 1. Tokenizer: biến thể viết tắt chưa được bảo vệ

- `2506.20793`, block 6: source có `(eg. for Qwern3-8b, 14.4\%)`; output ngắt sau `eg.` và tạo câu mới `for Qwern3-8b, 14.4\%).`.
- `2506.20795`, block 8: `e.\,g. natural moving ...` bị cắt sau `e.\,g.`. Khoảng cách LaTeX khiến mẫu `e.g.` hiện tại không khớp.
- Hướng sửa tối thiểu: thêm biến thể đã gặp vào regex abbreviation và regression test; không cần NLP model.

### 2. Tokenizer: initials và đánh số viết tay

- `2506.20791`, block 26: `{\it Acknowledgments} I.-M.I.` và `was supported by the Project ...` bị tách thành hai câu; cùng đoạn có các mục `i.`, `ii.`, `iii.` viết tay.
- `2506.20794` và `2506.20796` cũng có initials tác giả bị tách. Cần luật có ngữ cảnh, tránh bảo vệ mọi chữ cái + dấu chấm rồi vô tình gộp câu thật.

### 3. Cleaner: định dạng lồng trong command chưa biết

- `2506.20793`, figure `fig_1`: output còn `\caption{\textbf{...} ...}` vì cleaner giữ nguyên toàn bộ đối số caption.
- `2506.20795`: bảng được bọc bởi command khác vẫn còn textbf ở header. Các dạng cũ `{\it ...}`, `\bf`, `\LARGE` cũng ngoài danh sách cleanup hiện tại.
- Hướng sửa: whitelist các command chứa prose để làm sạch có chọn lọc; không mở đệ quy mọi đối số vì có thể làm hỏng math, URL hoặc tham số kỹ thuật.

### 4. Ranh giới Module 2/3: command chứa nhiều câu

- `2506.20794`: command `\abstract{...}` giữ nguyên nhiều câu trong một sentence. Bảo vệ mọi command là an toàn cho citation nhưng quá rộng cho command chứa prose.
- Nên để hierarchy nhận diện abstract/footnote hoặc thống nhất whitelist prose-bearing command với Hoàng; không bỏ bảo vệ citation để chữa lỗi này.

### 5. Hierarchy: nhận nhầm command cấu trúc

- `2506.20793`, block 28 và 33 chỉ có `\setlength{`; block kế tiếp bắt đầu `sep}{0pt}`. Scanner hierarchy nhận `\item` bên trong `\itemsep` như một list item vì thiếu ranh giới tên command.
- Cần sửa tại scanner của Module 3 với ranh giới tên lệnh và test tái hiện; Module 2 không thể phục hồi sau khi input đã bị cắt sai.

### 6. Hierarchy/cleanup: markup và vùng không phải prose

- Có sentence chỉ chứa `\end{proof}`, `\label{...}`, list options hoặc metadata affiliation/address; algorithm và ytableau/scalebox được đưa vào prose.
- `2506.20798` còn `\begin{comment}`/`\end{comment}` và nội dung trong vùng đó. Strip comment `%` không xử lý môi trường comment.
- Phải thống nhất cách xử lý các môi trường với Hoàng. Không xóa mù nội dung theorem/algorithm hoặc mọi môi trường lạ.

## Kết luận nghiệm thu

**Đã đáp ứng bước kiểm tra tối thiểu 10 bài thật: 11 bài TeX đã chạy và được xem mẫu thủ công, thêm 1 PDF-only. Chưa thể đánh dấu Module 2 hoàn toàn đạt chất lượng bàn giao**, vì còn lỗi ranh giới câu và giới hạn cleanup nêu trên. Trong lượt này chỉ kiểm tra và ghi nhận, chưa sửa hành vi parser.

Ưu tiên tiếp theo: (1) regex item của hierarchy; (2) biến thể eg./e.\,g.; (3) initials; (4) whitelist caption/abstract và dọn markup theo thỏa thuận Module 2/3. Giữ nguyên nguồn và output hiện tại làm baseline trước khi sửa.

## Tái hiện trên dữ liệu local

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -B data/module2-khang-review/review_sample.py
```

Script đọc các archive `/tmp/khang-2506.*v1.source` đã tải; không tải mạng. Các archive trong `/tmp` có thể bị hệ thống dọn; URL và SHA-256 đã được lưu để kiểm chứng khi tải lại.

## Cập nhật sửa lỗi ranh giới item — 2026-11-12

- Đã thêm `(?![A-Za-z])` sau `\\item` trong `hierarchy/builder.py`, kèm comment tiếng Việt. Đây là thay đổi ở Module 3 cần Hoàng lưu ý khi tích hợp.
- Test tái hiện trước sửa tạo 4 item thay vì 3; sau sửa nhận đúng 3 dạng item thật, giữ nguyên `\setlength{\itemsep}{0pt}` và quan hệ cha–con.
- Chạy lại source thật `2506.20793v1`: số node item giảm **12 → 10**, loại đúng hai item giả; cả hai lệnh itemsep không còn bị cắt. Output sau sửa: `data/module2-khang-review/2506.20793/after-item-boundary-fix.json`. Baseline `review.json` được giữ nguyên.
- Kiểm tra liên quan: **115 tests, 10 subtests pass**. Các lỗi/giới hạn khác trong báo cáo chưa được sửa ở đợt này. Lệnh `setlength` còn trong prose nguyên vẹn; loại lệnh trình bày này là bước cleanup riêng.

## Chạy lại toàn bộ mẫu sau sửa item — 2026-11-12

Đã chạy lại **cùng 12 bản v1** bằng dữ liệu local: 11 TeX và 1 PDF-only. Đối chiếu merged source bằng nhau trước khi chạy; giữ nguyên toàn bộ baseline `review.json` và `summary.json`.

| Bài | Sentence trước → sau | Item trước → sau | Đối chiếu cây |
|---|---:|---:|---|
| 2506.20788 | 520 → 520 | 36 → 36 | Giống hệt |
| 2506.20789 | 784 → 784 | 26 → 26 | Giống hệt |
| 2506.20790 | 368 → 368 | 42 → 42 | Giống hệt |
| 2506.20791 | 116 → 116 | 0 → 0 | Giống hệt |
| 2506.20792 | 1331 → 1331 | 62 → 62 | Giống hệt |
| 2506.20793 | 152 → 150 | 12 → 10 | Chỉ bài này thay đổi; hai itemsep không còn bị cắt |
| 2506.20794 | 222 → 222 | 0 → 0 | Giống hệt |
| 2506.20795 | 191 → 191 | 0 → 0 | Giống hệt |
| 2506.20796 | 154 → 154 | 0 → 0 | Giống hệt |
| 2506.20797 | 1582 → 1582 | 218 → 218 | Giống hệt |
| 2506.20798 | 263 → 263 | 12 → 12 | Giống hệt |
| 2506.20799 | 0 → 0 | 0 → 0 | Vẫn empty_source |

- Tổng sau sửa: **5.681 sentence, 702 formula, 116 figure/table**.
- Kiểm tra **1.580 đoạn prose**: cleanup/math vẫn idempotent; tokenizer giữ nguyên ký tự khi bỏ qua whitespace.
- Hai lệnh `\setlength{\itemsep}{0pt}` của 2506.20793 còn nguyên trong input prose, không còn fragment `\setlength{` hoặc `sep}{0pt}` do chia sai. Đây chưa phải sửa cleanup setlength.
- Các lỗi abbreviation, initials, caption và markup khác đã ghi ở baseline vẫn còn, chưa được xử lý.
- Output từng bài: `data/module2-khang-review/<id>/after-item-boundary-fix.json`; tổng hợp: `data/module2-khang-review/summary-after-item-boundary-fix.json`.

Tái hiện, không cần mạng hoặc archive `/tmp`:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -B data/module2-khang-review/rerun_after_item_fix.py
```

## Cập nhật vocab viết tắt — 2026-11-12

- Chuyển danh sách viết tắt ra `standardization/vocab.txt`, mỗi dòng là văn bản literal. Đọc một lần khi import, đường dẫn theo module, không theo working directory.
- Tạo pattern chung cho spacing whitespace/LaTeX giữa các phần viết tắt: ví dụ `e.g.`, `e. g.`, `e.\,g.`, `i.\,e.`; thêm biến thể từ vựng `eg.`. Nội dung đầu vào được giữ nguyên, không thay thế spacing.
- Chạy lại đúng hai đoạn baseline: `2506.20793` block 6 giảm **8 → 7 câu**, không tách `eg. for Qwern3-8b`; `2506.20795` block 8 giảm **23 → 22 câu**, không tách `e.\,g. natural moving`.
- Output đối chiếu: `data/module2-khang-review/abbreviation-vocab-verification.json`. Baseline giữ nguyên. Các thay đổi đếm câu ở bảng chạy lại trước đó là kết quả trước khi bổ sung vocab.
- Kiểm thử liên quan: **126 tests, 10 subtests pass**. Initials và các giới hạn cleanup/hierarchy khác chưa được sửa.

## Chạy lại toàn bộ mẫu sau vocab — 2026-11-12

Chạy `data/module2-khang-review/rerun_after_vocab_fix.py` trên cùng 11 bài TeX + 1 PDF-only. Source merged khớp baseline; không ghi đè các output cũ. Output mới: `<id>/after-vocab-fix.json`; tổng hợp `summary-after-vocab-fix.json` trong cùng thư mục dữ liệu.

- Tổng hiện tại: **5.676 sentence, 702 formula, 116 figure/table**. 1.580 đoạn prose tiếp tục đạt idempotence cleanup/math và bảo toàn ký tự tokenizer khi bỏ whitespace.
- So với lần sau sửa item: 2506.20793 **150 → 147 câu** (ba chỗ `eg.`); 2506.20795 **191 → 189 câu** (hai chỗ `e.\,g.`). Đã đọc đối chiếu cả năm chỗ thay đổi, đều nối lại phần ví dụ bị cắt sai. Các cây còn lại giống hệt lần sau sửa item.
- Hai vị trí itemsep vẫn được giữ nguyên; không tái xuất hiện fragment bị cắt.
- Bộ kiểm thử liên quan: **126 tests và 10 subtests pass**.

### Những lỗi/giới hạn vẫn xác nhận được ở output mới

1. **Initials:** 2506.20791 còn `{\it Acknowledgments} I.-M.I.` đứng trước câu mới `was supported ...`; 2506.20794 còn `L.B.`/`S.K.` đứng riêng; 2506.20796 còn `K.D.` và `and J.L.` tách khỏi `acknowledge ...`.
2. **Viết tắt khác chưa có vocab:** phát hiện thêm ở 2506.20788: `This research is funded by the U.S.` bị tách khỏi `Department of Energy ...`. Đây là viết tắt tên nước, khác initials người; cần bổ sung vocab hoặc luật phù hợp, không suy rằng bổ sung eg. đã xử lý toàn bộ viết tắt.
3. **Command chứa prose:** 2506.20794 còn command `\abstract{...}` nhiều câu được bảo vệ nguyên khối, chưa đạt granularity sentence bên trong.
4. **Cleanup lồng:** 2506.20793 và 2506.20795 vẫn còn `\textbf` bên trong figure/table được bọc bởi command khác (caption/command bọc bảng).
5. **Markup và cấu trúc:** 2506.20789 còn sentence `\end{proof}`; metadata, label, list options, algorithm và các môi trường chưa hỗ trợ vẫn đi vào prose.
6. **Comment environment:** 2506.20798 vẫn có `\begin{comment}` và nội dung `Assume that the receiver ...` trong sentence, dù đó là vùng cần loại trừ.

Kết luận: sửa vocab đã loại được năm ranh giới câu sai trong mẫu, không phát hiện thay đổi ngoài dự kiến ở các cây còn lại. Chưa thể khẳng định hết lỗi hoặc tính precision/recall vì chưa có gold segmentation cho toàn bộ corpus. Ưu tiên tiếp: initials và viết tắt còn thiếu, rồi thống nhất whitelist command chứa prose và xử lý môi trường với Module 3. Lượt này chỉ kiểm tra, không sửa thêm code parser.

## Sửa abstract/caption/proof/label/comment — 2026-11-12

### Thay đổi

- Module 3 loại `comment` environment trước khi đọc title/body và quét cấu trúc. Thiếu dấu đóng thì ghi warning và loại phần còn lại. Các ví dụ comment nằm trong verb/verbatim/lstlisting/minted được giữ tại bước loại comment; đây chưa phải bộ mô phỏng TeX/literal đầy đủ cho toàn pipeline.
- Module 3 mở command `abstract` bằng ngoặc cân bằng và xử lý từng đối số prose theo thứ tự; abstract environment được bỏ wrapper. Math/list bên trong abstract vẫn qua scanner cấu trúc.
- Module 2 cleanup đệ quy các đối số chữ của `caption`, gồm optional short caption và dạng có sao. Không đổi đối số command lạ, citation/ref hoặc math; figure vẫn một node.
- Module 3 bỏ label trực tiếp trong prose và wrapper proof/proof*. Nội dung proof vẫn giữ; optional proof heading được xem là metadata và không đưa vào prose. Không xóa label nằm trong math hoặc command lạ.
- Đây là thay đổi tại cả Module 2 và Module 3; Hoàng cần lưu ý khi tích hợp. Algorithm và wrapper theorem/lemma tùy biến chưa được thay đổi.

### Kiểm chứng trên cùng mẫu thật

Đã chạy lại 11 bài TeX và 1 PDF-only, không ghi đè baseline. Output: `data/module2-khang-review/<id>/after-prose-structure-fix.json`; tổng hợp `summary-after-prose-structure-fix.json`; chạy lại bằng `rerun_after_prose_structure_fix.py` trong cùng thư mục.

- 2506.20789: **7 → 0** sentence chỉ chứa `\end{proof}`; nội dung chứng minh vẫn được xử lý.
- 2506.20793: caption của fig_1 bỏ được `\textbf`; không còn command label đứng riêng.
- 2506.20794: abstract nhiều câu đã được mở và tách, giữ inline math/citation; còn metadata institute/subtitle và markup par chưa thuộc phạm vi đợt này.
- 2506.20798: loại một subsection và **5 formula** trong vùng comment. Câu `Assume that the receiver ...` có hai lần trong nguồn (một lần hiển thị, một lần bị ẩn): output giảm từ **2 → 1**, giữ đúng bản hiển thị. Các nhận xét trước đây không phân biệt hai bản đã được làm rõ tại đây.
- Tổng sau sửa: **5.635 sentence, 697 formula, 116 figure/table**, 1.574 đoạn prose. Kiểm tra idempotence cleanup/math và bảo toàn ký tự tokenizer khi bỏ whitespace tiếp tục đạt.
- Bộ kiểm thử: **140 tests và 10 subtests pass**, gồm malformed caption, comment thiếu dấu đóng, comment chứa cấu trúc ẩn, abstract nhiều đối số, math/citation, proof/label và figure caption.

### Giới hạn còn lại

Initials và U.S. chưa sửa. Định dạng nằm sâu trong command bọc bảng như resizebox vẫn giữ nguyên vì chưa có handler cho đối số chứa nội dung của command đó. Wrapper theorem/remark tùy biến, algorithm, metadata và list options còn cần quyết định riêng. Nhãn nằm bên trong command chưa biết vẫn được giữ để tránh mất dữ liệu. Việc loại vùng comment diễn ra sau gather, nên include nằm trong comment vẫn có thể được gather đọc trước đó; mở rộng gathering nằm ngoài đợt sửa này.
