---
kernelspec:
  name: python3
  display_name: Python 3
---
# 1.7 Chất lượng dữ liệu và bảng dữ liệu

> **Người phụ trách:** Huỳnh Nhi (`@TrinhNhi3011`)  
> **Reviewer:** Hoàng (`huynhhuyhoang2006@gmail.com`)

```{code-cell} python
:tags: [remove-cell]
# Tìm gốc repo sách (thư mục có myst.yml) để import book.lib
import sys
from pathlib import Path
root = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p / "myst.yml").exists())
sys.path.insert(0, str(root))
from book.lib import huynhnhi_charts as h
```

```{code-cell} python
:tags: [remove-input]
issues = h.load("quality_issues.csv")
runs = h.manifest()["runs"]
n_runs = len(runs)
n_validation = int((issues["report"] == "validation").sum())
n_quality = int((issues["report"] == "quality").sum())
n_quality_papers = issues.loc[issues["report"] == "quality", "arxiv_id"].dropna().astype(str).str.strip().replace("", float("nan")).dropna().nunique()
# Đổi số numpy sang số Python để {eval} hiển thị gọn (50.0 thay vì np.float64(50.0))
import numpy as np
for _k, _v in list(globals().items()):
    if isinstance(_v, np.generic):
        globals()[_k] = _v.item()
```

> [!NOTE]
> Số liệu trong mục này lấy từ dữ liệu crawl thật của cả nhóm tại mốc ngày 05/11/2026. Lúc đó quá trình crawl chưa hoàn tất, nên các con số chỉ phản ánh phần dữ liệu đã thu được đến mốc này.

Mục này trả lời hai câu hỏi về dữ liệu thu được ở Milestone 1: dữ liệu có **đúng cấu trúc** không, và dữ liệu đã **đủ** chưa. Sau đó mục này trình bày cách các bản ghi `Result` của pipeline được ánh xạ sang hai bảng `papers` và `references` mà đề yêu cầu. Các chỉ số tổng hợp như tỷ lệ thành công hay số reference trung bình nằm ở mục 1.6 và không được nhắc lại ở đây.

## 1. Hai báo cáo: `validation.json` và `quality.json`

Mỗi lần runner lưu checkpoint, nó ghi bộ file bàn giao (handoff) trong `runs/<run_id>/`: `scope.json`, `accepted_results.jsonl`, `validation.json` và `quality.json`. Hai file sau đều có dạng `{"valid": bool, "issues": [...]}`, nhưng trả lời hai câu hỏi khác nhau.

| | `validation.json` | `quality.json` |
|---|---|---|
| Câu hỏi | Bộ handoff có **đúng cấu trúc và nhất quán** không? | Dữ liệu đã **thu thập đủ** chưa? |
| Hàm kiểm tra | `validate_handoff` (`validation/corpus.py`) và `validate_result` (`validation/results.py`) | `validate_corpus` và `paper_issues` (`validation/corpus.py`) |
| Khi `valid = false` | Không được dùng bộ dữ liệu; runner **không ghi** `scope.json` và `accepted_results.jsonl` mới | Dữ liệu vẫn dùng được nhưng còn việc chưa xong; phải ghi rõ phạm vi khi báo cáo |
| Ví dụ issue | Sai kiểu trường, trùng task, kết quả thuộc run khác, nguồn của phiên bản chưa được metadata xác minh | Discovery còn ID chưa xử lý, metadata chưa `success`, phiên bản thiếu TeX, references chưa xong |

Như vậy một run có thể có `validation.valid = true` (dữ liệu đúng) nhưng `quality.valid = false` (dữ liệu chưa đủ), chẳng hạn khi run dừng giữa chừng do giới hạn thời gian của Colab. Ngược lại, chỉ cần `validation.valid = false` là toàn bộ handoff không được dùng.

Các bộ phận khác của hệ thống đều dựa trên quy ước này. Bộ thu gom (`collection/inspector.py`) chỉ nhận một run khi `validation.json` đúng bằng `{"valid": true, "issues": []}`. Trước khi xuất `quality.json`, runner tự chạy lại `validate_handoff`, nên mọi issue cấu trúc cũng xuất hiện trong `quality.json`.

### 1.1. Các luật kiểm tra cấu trúc (validation)

Mỗi `Result` được kiểm tra riêng trước, sau đó cả tập được kiểm tra chéo:

1. **Kiểu và trường**: đúng schema 1.2, không thiếu và không thừa trường, giá trị JSON hữu hạn, `run_id` chỉ gồm chữ, số, `_`, `-`.
2. **Trạng thái hợp lệ theo bước**: ví dụ `unsupported` chỉ có ở `sources`, `empty` chỉ có ở `references`, `not_found` không có ở `discovery`.
3. **Chủ thể**: `discovery` có `arxiv_id = null` (một kết quả cho cả dải); các bước khác phải có arXiv ID hợp lệ. Chỉ `sources` có `version ≥ 1`.
4. **Nhất quán giữa `status`, `data` và `error`**: `failed`/`not_found` phải có `data = null` và có `error`; `success`/`empty`/`unsupported` không được có `error` hay `missing_fields`; mọi JSON Pointer trong `missing_fields` phải trỏ tới trường có thật.
5. **Thời gian**: `finished_at` không sớm hơn `started_at`; `attempts` và `duration_seconds` không âm.
6. **Kiểm tra chéo trong handoff**: mỗi task (owner, step, arxiv_id, version) chỉ có một kết quả được chấp nhận; mọi kết quả thuộc đúng run và đúng chế độ mock/thật của `scope.json`; bài nằm trong dải được giao cho owner; số đếm trong `scope.json` khớp; mỗi phiên bản có nguồn phải nằm trong `versions` của metadata đã chấp nhận; cờ `versions_complete` của scope khớp với metadata.

Luật 6 bảo đảm rằng run thật không bao giờ trộn với dữ liệu mock: nếu có, đó là lỗi cấu trúc, không bị âm thầm lọc bỏ.

### 1.2. Các luật kiểm tra độ đầy đủ (quality)

Với mỗi dải ID được giao, kết quả discovery phải không lỗi và không còn `unresolved_ids`. Với mỗi bài trong scope (`paper_issues`):

- metadata phải có trạng thái `success`;
- mọi phiên bản trong `versions` phải có `Result` sources với trạng thái `success`;
- references phải có trạng thái `success` hoặc `empty` (bài thật sự không có tài liệu tham khảo).

Luật này khắt khe hơn những gì được chấp nhận về cấu trúc: một phiên bản `unsupported` là kết quả hợp lệ (không có lỗi validation) nhưng vẫn bị tính là "TeX source unavailable or incomplete" trong `quality.json`, vì bài đó không có đủ nguồn TeX cho Milestone 2.

### 1.3. Kết quả trên dữ liệu đã thu

Dữ liệu được đọc từ {eval}`n_runs` run, mỗi owner một run chính thức. Kết quả của hai báo cáo:

- Số issue cấu trúc (`validation.json`): **{eval}`n_validation`**. Bộ handoff chỉ hợp lệ khi con số này bằng 0.
- Số issue về độ đầy đủ (`quality.json`): **{eval}`n_quality`**, liên quan tới **{eval}`n_quality_papers`** bài. Dữ liệu chỉ đầy đủ khi con số này bằng 0.

Bảng dưới gom các issue theo báo cáo, bước và nội dung.

```{code-cell} python
:tags: [remove-input]
:label: tbl-1-7-issues
:caption: Số issue theo báo cáo, bước và nội dung
h.issues_by_group(issues)
```

## 2. Ánh xạ sang bảng `papers`

Đề quy định bảng `papers` có một dòng cho mỗi bài. Mỗi dòng được lấy từ `data` của `Result` có `step = "metadata"` (mục 1.3):

| Cột của đề | Kiểu theo đề | Lấy từ `Result` metadata |
|---|---|---|
| `arxiv_id` | string, non-null | `data.arxiv_id` |
| `title` | string | `data.title` |
| `authors` | list of strings | `data.authors` |
| `submission_date` | ISO string | `data.submission_date` (ngày nộp v1) |
| `revised_dates` | list of strings | `data.revised_dates` (ngày nộp v2…vN) |
| `venue` | string, nullable | `data.venue` (lấy từ `journal-ref`) |

Các trường còn lại của `Result` (`versions`, `versions_complete`, `missing_fields`, `provenance`) không đưa vào bảng nhưng được dùng để kiểm tra chất lượng như trên. [](#tbl-1-7-papers-sample) là vài dòng thật, sinh từ dữ liệu khi build sách (cột danh sách được hiển thị dạng JSON).

```{code-cell} python
:tags: [remove-input]
:label: tbl-1-7-papers-sample
:caption: Ví dụ các dòng của bảng papers
h.load("papers_sample.csv")
```

## 3. Ánh xạ sang bảng `references`

Bảng `references` có một dòng cho mỗi cặp (bài trích dẫn, bài được trích dẫn). Mỗi dòng lấy từ từ điển `data.references` của `Result` có `step = "references"` (mục 1.5), với khóa là arXiv ID đã chuẩn hóa của bài được trích dẫn:

| Cột của đề | Kiểu theo đề | Lấy từ `Result` references |
|---|---|---|
| `arxiv_id` | string, non-null | `arxiv_id` của `Result` (bài trích dẫn) |
| `ref_arxiv_id` | string, non-null | khóa của `data.references` |
| `ref_title` | string | `data.references[id].title` |
| `ref_authors` | list of strings | `data.references[id].authors` |
| `ref_submission_date` | string | `data.references[id].submission_date` (lấy từ arXiv API, không dùng `publicationDate` của Semantic Scholar) |
| `ref_s2_id` | string | `data.references[id].semantic_scholar_id` |

Chỉ những tài liệu tham khảo **có arXiv ID** mới trở thành dòng của bảng `references`, vì bảng này dùng arXiv ID làm khóa và là tập đối sánh cho Milestone 3. Danh sách đầy đủ mà Semantic Scholar trả về, kể cả các mục không có arXiv ID, vẫn được giữ cục bộ trong `data.raw_references` theo yêu cầu của đề, nhưng không đưa vào bảng và không đưa lên repo.

```{code-cell} python
:tags: [remove-input]
:label: tbl-1-7-references-sample
:caption: Ví dụ các dòng của bảng references
h.load("references_sample.csv")
```

## 4. Ghi chú về việc công bố dữ liệu

Đề không cho phép nộp dữ liệu ở Milestone 1 và 2. Bảng `papers` và `references` sẽ được xuất thành Parquet trên Hugging Face ở bước cuối. Repo sách chỉ chứa các file tổng hợp nhỏ trong `data/huynhnhi/` (do `scripts/prepare_huynhnhi.py` sinh ra) và vài dòng ví dụ ở trên.

## 5. Tài liệu tham khảo

- Bryan, P. & Nottingham, M. (2013). *JavaScript Object Notation (JSON) Pointer*, RFC 6901. https://www.rfc-editor.org/rfc/rfc6901
- Semantic Scholar. *Academic Graph API*. https://api.semanticscholar.org/api-docs/graph
