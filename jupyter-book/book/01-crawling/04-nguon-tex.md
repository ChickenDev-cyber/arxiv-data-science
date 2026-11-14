---
kernelspec:
  name: python3
  display_name: Python 3
---
# 1.4 Nguồn TeX/BibTeX và loại hình

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
from IPython.display import Markdown
src = h.load("sources_per_version.csv")
n_versions = len(src)
n_papers_src = src["arxiv_id"].nunique()
n_ok = int((src["status"] == "success").sum())
n_unsupported = int((src["status"] == "unsupported").sum())
meta = h.load("metadata_per_paper.csv")
n_expected = int(meta["version_count"].sum())
n_pending_src = n_expected - n_versions
pct_ok = round(n_ok / n_versions * 100, 2) if n_versions else 0
pct_unsupported = round(n_unsupported / n_versions * 100, 2) if n_versions else 0
checked = src[src["invariant_ok"].isin([True, False, "True", "False"])]
n_invariant_checked = len(checked)
n_invariant_bad = int(checked["invariant_ok"].astype(str).eq("False").sum())
ok = src[src["status"] == "success"]
total_before = ok["extracted_bytes_before_filter"].sum()
pct_retained = round(ok["retained_source_bytes"].sum() / total_before * 100, 2) if total_before else 0
pct_fig = round(ok["removed_figure_bytes"].sum() / total_before * 100, 2) if total_before else 0
median_before_kib = round(ok["extracted_bytes_before_filter"].median() / 1024, 1) if len(ok) else 0
median_after_kib = round(ok["retained_source_bytes"].median() / 1024, 1) if len(ok) else 0
# Đổi số numpy sang số Python để {eval} hiển thị gọn (50.0 thay vì np.float64(50.0))
import numpy as np
for _k, _v in list(globals().items()):
    if isinstance(_v, np.generic):
        globals()[_k] = _v.item()
```

> [!NOTE]
> Số liệu trong mục này lấy từ dữ liệu crawl thật của cả nhóm tại mốc ngày 05/11/2026. Lúc đó quá trình crawl chưa hoàn tất, nên các con số chỉ phản ánh phần dữ liệu đã thu được đến mốc này.

Bước T3 tải gói nguồn mà tác giả đã nộp lên arXiv cho **từng phiên bản** của mỗi bài, giải nén an toàn, chỉ giữ lại file `.tex` và `.bib`, và ghi lại năm số đo dung lượng. Đây là đầu vào của Milestone 2 (Chương 2). Mã nguồn nằm trong `src/arxiv_pipeline/sources/`: `api.py` tải file, `extractor.py` nhận diện định dạng, `handlers/` giải nén theo từng định dạng, và `service.py` đóng gói kết quả thành `Result`.

## 1. Tải nguồn theo từng phiên bản

Nguồn của phiên bản N được tải từ endpoint:

```text
https://export.arxiv.org/src/<arxiv_id>v<N>
```

Danh sách phiên bản lấy từ `Result` metadata đã được chấp nhận (mục 1.3), nên mỗi bài có N phiên bản sẽ sinh ra N bản ghi `Result` với `step = "sources"` và `version = N`. Mỗi phiên bản được xử lý độc lập: một phiên bản lỗi không làm mất các phiên bản khác của cùng bài.

Client tải theo luồng (streaming) bằng `urllib.request`, giãn cách tối thiểu 3 giây giữa hai request như các bước khác, và từ chối file lớn hơn 200 MiB. Lần tải và các lần giải nén lại dùng chung một ngân sách tối đa 3 lần gửi request. Một file gzip bị cắt cụt (thường do đường truyền) được coi là lỗi có thể thử lại; các lỗi cấu trúc khác thì không.

Metadata đã chấp nhận liệt kê tổng cộng {eval}`n_expected` phiên bản. Tại mốc dữ liệu này, {eval}`n_versions` phiên bản của {eval}`n_papers_src` bài đã có kết quả nguồn được chấp nhận, còn {eval}`n_pending_src` phiên bản chưa xử lý xong (kết quả `failed`/`not_found` chỉ nằm trong `events.jsonl` và được thử lại, không vào handoff). Trong số đã chấp nhận, {eval}`n_ok` phiên bản `success` ({eval}`pct_ok`%) và {eval}`n_unsupported` phiên bản `unsupported` ({eval}`pct_unsupported`%).

## 2. Nhận diện định dạng

arXiv trả nguồn ở nhiều dạng mà không ghi rõ định dạng trong tên file, vì vậy hàm `detect_source_kind` đọc vài byte đầu (magic bytes) để phân loại.

| Loại nguồn (source_kind) | Cách nhận diện | Xử lý |
|---|---|---|
| `tar_gzip` | Bắt đầu bằng `1f 8b` (gzip) và bên trong là tar | Giải nén tar, lọc file |
| `tar` | Tar không nén | Giải nén tar, lọc file |
| `gzip_tex` | Gzip của **một** file văn bản (bài chỉ có một file `.tex`) | Giải nén thành một file `.tex` |
| `tex` | Văn bản thuần, không phải trang HTML | Giữ nguyên làm file `.tex` |
| `pdf_only` | Bắt đầu bằng `%PDF-` (tác giả chỉ nộp PDF) | `unsupported`, giữ bản ghi rỗng |
| `other` | Không thuộc các loại trên (gồm gói DOCX) | DOCX được xác minh thì `unsupported`; còn lại là lỗi `invalid_source` |

Một file văn bản được coi là TeX khi không chứa byte NUL, giải mã được bằng UTF-8 hoặc latin-1, và không phải trang HTML (để tránh nhận nhầm trang báo lỗi thành mã nguồn). Bước này không bắt buộc phải có lệnh TeX ở đầu file, vì nhiều bài mở đầu bằng một đoạn chú thích dài; việc phân tích cú pháp TeX thuộc về Milestone 2.

```{code-cell} python
:tags: [remove-input]
:label: fig-1-4-source-kind
:caption: Phân bố source_kind theo phiên bản
h.fig_source_kind(src);
```

## 3. Lọc file: giữ `.tex`/`.bib`, giữ lệnh hình trong TeX

Sau khi giải nén, mỗi file được phân vào một trong ba nhóm theo phần mở rộng (không phân biệt hoa thường):

| Nhóm | Phần mở rộng | Xử lý |
|---|---|---|
| Giữ lại | `.tex`, `.bib` | Lưu vào thư mục nguồn của phiên bản |
| Hình | `.png`, `.pdf`, `.jpg`, `.jpeg`, `.eps`, `.ps`, `.svg`, `.gif`, `.bmp`, `.tiff`, `.tif` | Bỏ, cộng vào `removed_figure_bytes` |
| Khác | mọi file còn lại (`.sty`, `.cls`, `.bbl`, `README`, …) | Bỏ, cộng vào `removed_other_bytes` |

Chỉ **file** hình bị bỏ, còn **nội dung TeX được giữ nguyên**: các lệnh `\begin{figure}`, `\includegraphics{...}`, `\caption{...}` vẫn còn trong file `.tex`. Milestone 2 cần các lệnh này để dựng nút Figure trong cây phân cấp, nên bước T3 không được sửa nội dung TeX.

Một lưu ý cho Milestone 2: file `.bbl` (danh mục tham khảo do BibTeX sinh sẵn) thuộc nhóm "khác" nên bị bỏ. Những bài chỉ khai báo tài liệu tham khảo bằng `\bibitem` ngay trong `.tex` thì vẫn giữ được, còn những bài chỉ có `.bbl` mà không có `.bib` sẽ không còn danh mục tham khảo dạng file.

## 4. Phiên bản không có TeX vẫn được ghi nhận

Một phiên bản được ghi `status = "unsupported"` khi tác giả chỉ nộp PDF (`pdf_only`), nộp gói Word (DOCX), hoặc gói tar không chứa file `.tex` nào. Bản ghi này **không bị bỏ qua**: nó có `files = []`, `retained_source_bytes = 0` và `error = null`. Đề Milestone 2 yêu cầu phiên bản không có TeX phải được ghi là rỗng và tính vào độ phủ phiên bản, nên bản ghi rỗng này là cần thiết. Các trạng thái của `Result` sources:

| Trạng thái (status) | Khi nào |
|---|---|
| `success` | Có ít nhất một file `.tex` sau khi lọc |
| `unsupported` | PDF-only, DOCX, hoặc gói không có `.tex`; vẫn giữ bản ghi rỗng |
| `not_found` | arXiv xác nhận không có nguồn cho phiên bản này |
| `failed` | Lỗi mạng, lỗi định dạng (`invalid_source`), gói không an toàn (`unsafe_archive`) hoặc vượt giới hạn (`extract_limit`) |

## 5. Giải nén an toàn

Gói nguồn là dữ liệu do người ngoài tải lên, nên các handler không dùng `extractall` mà kiểm tra từng thành phần trước khi ghi ra đĩa:

- **Đường dẫn**: từ chối đường dẫn tuyệt đối, có `..`, dấu `\`, ký tự ổ đĩa Windows (`C:`) hoặc byte NUL, để không ghi được ra ngoài thư mục đích (lỗ hổng *path traversal*). Lỗi này được ghi là `unsafe_archive`.
- **Loại thành phần**: chỉ chấp nhận file thường và thư mục; symlink, hardlink và file thiết bị bị từ chối.
- **Trùng lặp**: hai thành phần cùng đường dẫn, hoặc một đường dẫn vừa là file vừa là thư mục, bị từ chối.
- **Giới hạn kích thước** (chống *zip bomb*): tối đa 10.000 thành phần, 200 MiB mỗi file và 1 GiB tổng sau giải nén. Dung lượng được đếm trong lúc đọc dữ liệu chứ không chỉ tin vào header. Vượt giới hạn thì ghi `extract_limit`.

Giải nén diễn ra trong thư mục tạm; chỉ khi thành công, thư mục kết quả mới được chuyển sang vị trí chính thức. Vì vậy một lần chạy lỗi không để lại nguồn dở dang.

## 6. Năm số đo dung lượng

Mỗi `Result` sources có năm số đo trong `metrics`:

| Trường | Ý nghĩa |
|---|---|
| `download_bytes` | Kích thước file tải về (đã nén) |
| `extracted_bytes_before_filter` | Tổng dung lượng mọi file sau giải nén, trước khi lọc |
| `retained_source_bytes` | Tổng dung lượng các file `.tex`/`.bib` được giữ |
| `removed_figure_bytes` | Tổng dung lượng file hình bị bỏ |
| `removed_other_bytes` | Tổng dung lượng các file khác bị bỏ |

Ba phần cuối phải cộng lại bằng dung lượng trước lọc:

$$
\text{extracted\_bytes\_before\_filter} = \text{retained\_source\_bytes} + \text{removed\_figure\_bytes} + \text{removed\_other\_bytes}
$$

Với phiên bản `unsupported`, dung lượng trước lọc và hai phần bị bỏ là `null` (không giải nén nên không đo), còn `retained_source_bytes = 0`. Bất biến được kiểm tra trên {eval}`n_invariant_checked` phiên bản có đủ số đo; số phiên bản vi phạm là {eval}`n_invariant_bad`.

Các số đo ở mục này tính **theo phiên bản**. Kích thước trung bình **mỗi bài** trước và sau khi lọc hình, theo đúng câu hỏi của đề, được trình bày ở mục 1.6.

Trên các phiên bản `success`, trung vị dung lượng trước lọc là {eval}`median_before_kib` KiB và sau lọc là {eval}`median_after_kib` KiB ([](#fig-1-4-size)). Tính trên tổng số byte, phần được giữ lại chiếm {eval}`pct_retained`%, còn file hình chiếm {eval}`pct_fig`% ([](#tbl-1-4-removed)). Như vậy phần lớn dung lượng của một gói nguồn là hình, trong khi văn bản TeX mà Milestone 2 cần chỉ chiếm phần nhỏ.

```{code-cell} python
:tags: [remove-input]
:label: fig-1-4-size
:caption: Kích thước nguồn mỗi phiên bản trước và sau khi lọc (trục dọc thang log)
h.fig_size_before_after(src);
```

```{code-cell} python
:tags: [remove-input]
:label: tbl-1-4-removed
:caption: Tỷ lệ byte được giữ, bị loại do hình và bị loại vì lý do khác
h.removed_bytes_table(src)
```

## 7. Tài liệu tham khảo

- arXiv. *Accessing arXiv source files*. https://info.arxiv.org/help/bulk_data.html
- arXiv. *Terms of Use for arXiv APIs*. https://info.arxiv.org/help/api/tou.html
- Python Software Foundation. *tarfile: Extraction filters*. https://docs.python.org/3/library/tarfile.html#extraction-filters
