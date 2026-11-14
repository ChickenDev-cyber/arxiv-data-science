---
kernelspec:
  name: python3
  display_name: Python 3
---
# 1.3 Metadata và lịch sử phiên bản

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
meta = h.load("metadata_per_paper.csv")
n_papers = len(meta)
n_success = int((meta["status"] == "success").sum())
n_partial = int((meta["status"] == "partial").sum())
n_not_found = int((meta["status"] == "not_found").sum())
n_failed = int((meta["status"] == "failed").sum())
n_scope = h.scope_papers()
n_pending = n_scope - n_papers
pct_covered = round(n_papers / n_scope * 100, 2) if n_scope else 0
pct_success = round(n_success / n_papers * 100, 2) if n_papers else 0
pct_versions_complete = round(meta["versions_complete"].mean() * 100, 2) if n_papers else 0
mean_versions = round(meta["version_count"].mean(), 2) if n_papers else 0
max_versions = int(meta["version_count"].max()) if n_papers else 0
pct_multi = round((meta["version_count"] >= 2).mean() * 100, 2) if n_papers else 0
pct_venue = round(meta["has_venue"].mean() * 100, 2) if n_papers else 0
n_date_order = int(meta["date_order_issue"].sum())
# Đổi số numpy sang số Python để {eval} hiển thị gọn (50.0 thay vì np.float64(50.0))
import numpy as np
for _k, _v in list(globals().items()):
    if isinstance(_v, np.generic):
        globals()[_k] = _v.item()
```

> [!NOTE]
> Số liệu trong mục này lấy từ dữ liệu crawl thật của cả nhóm tại mốc ngày 05/11/2026. Lúc đó quá trình crawl chưa hoàn tất, nên các con số chỉ phản ánh phần dữ liệu đã thu được đến mốc này.

Bước T2 của pipeline lấy metadata của từng bài báo đã được khám phá ở bước T1 (mục 1.2): tiêu đề, danh sách tác giả, nơi công bố và **ngày nộp của từng phiên bản**. Kết quả của mỗi bài là một bản ghi `Result` với `step = "metadata"` theo schema 1.2. Chính bản ghi này quyết định bước T3 (mục 1.4) phải tải nguồn của những phiên bản nào. Mã nguồn nằm trong `src/arxiv_pipeline/metadata/`: `api.py` gửi request và phân tích XML, `service.py` đóng gói kết quả thành `Result`.

## 1. Nguồn dữ liệu: OAI-PMH định dạng `arXivRaw`

arXiv cung cấp hai cách đọc metadata bằng máy: Atom API (`export.arxiv.org/api/query`) và giao thức OAI-PMH. Nhóm chọn OAI-PMH với định dạng `arXivRaw` vì những khác biệt sau.

| Tiêu chí | Atom API | OAI-PMH `arXivRaw` |
|---|---|---|
| Ngày nộp v1 | Có (`published`) | Có |
| Ngày nộp v2, v3, … | **Không**, chỉ có ngày cập nhật cuối (`updated`) | **Có**, mỗi thẻ `<version>` kèm `<date>` riêng |
| Số phiên bản | Chỉ biết phiên bản mới nhất | Liệt kê đủ v1…vN |
| Nơi công bố | `journal_ref` | `journal-ref` |
| Bài đã bị xóa | Không rõ ràng | Header có `status="deleted"` |

Đề yêu cầu bảng `papers` có cả `submission_date` và `revised_dates`, tức ngày của mọi phiên bản sửa đổi. Atom API chỉ cho biết ngày v1 và ngày của phiên bản cuối, nên không chứng minh được ngày của các phiên bản ở giữa. `arXivRaw` liệt kê từng phiên bản với ngày riêng, vì vậy đây là nguồn có đủ thông tin.

Mỗi bài cần đúng một request `GetRecord`:

```text
https://oaipmh.arxiv.org/oai?verb=GetRecord&metadataPrefix=arXivRaw&identifier=oai:arXiv.org:<arxiv_id>
```

Phần lõi của một phản hồi có dạng (rút gọn, minh họa):

```xml
<arXivRaw>
  <id>2409.03594</id>
  <title>...</title>
  <authors>A. Nguyen (1), B. Tran (1 and 2) ((1) Univ X, (2) Lab Y)</authors>
  <journal-ref>...</journal-ref>
  <version version="v1"><date>Thu, 5 Sep 2024 17:59:59 GMT</date></version>
  <version version="v2"><date>Tue, 1 Oct 2024 08:00:00 GMT</date></version>
</arXivRaw>
```

Client chỉ dùng thư viện chuẩn của Python (`urllib.request`, `xml.etree.ElementTree`), không dùng `requests`. Client áp dụng chung quy tắc giãn cách và thử lại với bước T1: tối thiểu 3 giây giữa hai request theo điều khoản sử dụng API của arXiv, tối đa 3 lần thử. Chỉ HTTP 429 được ghi là `rate_limited`. Các mã 500, 502, 503, 504 là `remote_error` có thể thử lại (503 là cơ chế điều tiết lưu lượng của OAI-PMH). Lỗi kết nối là `network_error`. Client tôn trọng header `Retry-After`, và giữa các lần thử thời gian chờ tăng dần 3 rồi 6 giây.

## 2. Kiểm tra phản hồi trước khi tin

Parser từ chối mọi phản hồi không chắc chắn nói về đúng bài đang hỏi, thay vì đoán:

- Phản hồi rỗng, lớn hơn 16 MiB, có DTD hoặc `<!ENTITY>` (để chống tấn công XML), hoặc không phải XML hợp lệ thì bị ghi là `invalid_payload`.
- Phần tử `<request>` và header của record phải nêu đúng `oai:arXiv.org:<arxiv_id>`, và `<id>` trong `arXivRaw` phải trùng với ID được hỏi.
- Khi OAI trả lỗi `idDoesNotExist` cho đúng ID được hỏi, hoặc record có `status="deleted"`, kết quả là `not_found`.
- Một trường xuất hiện hai lần (ví dụ hai `<title>`), một phiên bản có hai `<date>`, nhãn phiên bản không đúng dạng `vN` hoặc một phiên bản bị lặp đều bị ghi là `invalid_payload`.

## 3. Các trường lấy được

| Trường trong `Result.data` | Lấy từ `arXivRaw` | Ghi chú |
|---|---|---|
| `arxiv_id` | `<id>` | Đã đối chiếu với ID được hỏi |
| `title` | `<title>` | Gộp khoảng trắng; rỗng thì là `null` |
| `authors` | `<authors>` | Danh sách tên, theo quy tắc tách ở dưới |
| `submission_date` | `<date>` của v1 | Chuẩn ISO 8601 UTC, ví dụ `2024-09-05T17:59:59Z` |
| `revised_dates` | `<date>` của v2…vN | Theo thứ tự phiên bản |
| `venue` | `<journal-ref>` | `null` nếu bài chưa đăng ở đâu |
| `versions` | mọi `<version>` | Danh sách `{version, submitted_at}`, sắp theo số phiên bản |
| `versions_complete` | tính toán | Xem mục lịch sử phiên bản |

Ngày dạng `Thu, 5 Sep 2024 17:59:59 GMT` được đổi sang UTC. Ngày không đọc được hoặc không có múi giờ được để `null`; parser không tự gán múi giờ.

## 4. Quy tắc tách tác giả

Trường `<authors>` của arXiv là chuỗi tự do do tác giả nhập, nên hàm `_split_authors` xử lý theo các quy tắc sau:

1. Dấu phẩy và chữ ` and ` dùng để tách các tên.
2. Phần trong ngoặc tròn là đơn vị công tác và bị bỏ, kể cả ngoặc lồng nhau. Ví dụ `A (1), B (1 and 2), C (2) ((1) Univ X, (2) Lab Y)` cho ra `["A", "B", "C"]`.
3. Dấu phẩy nằm trong nhóm LaTeX `{...}` không tách tên.
4. Hậu tố `Jr.`, `Sr.`, `II`, `III`, `IV`… được nối vào tên đứng trước (`Smith, Jr.` là một người), để không làm tăng số tác giả.
5. Tên nhóm hợp tác, như `ATLAS Collaboration: A, B` hay `A, B, for the ATLAS Collaboration`, được giữ thành một mục riêng.
6. Hàm trả `null` (không biết) thay vì một danh sách sai khi trường rỗng, ngoặc không cân, có dấu `;` hoặc `:` không thuộc tên, hoặc danh sách bị cắt bằng `et al.`.

Nguyên tắc chung là **thà ghi thiếu còn hơn ghi sai**. Một danh sách tác giả `null` sẽ hiện trong `missing_fields` và được thống kê, còn một danh sách sai thì không ai phát hiện được.

## 5. Trạng thái của Result metadata

| Trạng thái (status) | Điều kiện | Trường data | Trường error |
|---|---|---|---|
| `success` | Phân tích được, không thiếu trường bắt buộc và `versions_complete = true` | đầy đủ | `null` |
| `partial` | Phân tích được nhưng thiếu trường (`missing_fields` khác rỗng) hoặc lịch sử phiên bản chưa đủ | có | `null` |
| `not_found` | arXiv xác nhận không có bài (`idDoesNotExist` hoặc `deleted`) | `null` | `not_found` |
| `failed` | Mọi lỗi còn lại: mạng, 429, 5xx, phản hồi không hợp lệ | `null` | mã lỗi tương ứng |

Lỗi mạng **không bao giờ** được ghi thành `not_found`; chỉ khi arXiv trả lời rõ ràng rằng bài không tồn tại thì mới kết luận như vậy. Các trường bắt buộc được kiểm tra là `title`, `authors`, `submission_date`, ngày của từng phiên bản và dãy phiên bản. Trường thiếu được ghi bằng JSON Pointer tính từ gốc `Result`, ví dụ `/data/authors` hay `/data/versions/1/submitted_at`. `venue` không bắt buộc vì nhiều bài chưa được đăng tạp chí.

Bộ handoff chỉ nhận kết quả `success` hoặc `partial` cho bước metadata. Kết quả `not_found`/`failed` vẫn được ghi trong `events.jsonl` và được runner thử lại ở lượt sau, nên chúng không xuất hiện trong bảng dưới. Trong {eval}`n_scope` bài thuộc scope, {eval}`n_papers` bài ({eval}`pct_covered`%) đã có metadata được chấp nhận; {eval}`n_pending` bài còn lại chưa có kết quả được chấp nhận tại mốc dữ liệu này. Trong số đã chấp nhận, {eval}`n_success` bài `success` ({eval}`pct_success`%) và {eval}`n_partial` bài `partial` ([](#tbl-1-3-status)).

```{code-cell} python
:tags: [remove-input]
:label: tbl-1-3-status
:caption: Số bài theo trạng thái metadata
h.metadata_status_table(meta)
```

[](#tbl-1-3-missing) đếm số bài thiếu từng trường; chỉ số phiên bản được gộp lại thành `/data/versions/*/submitted_at`.

```{code-cell} python
:tags: [remove-input]
:label: tbl-1-3-missing
:caption: Số bài thiếu theo từng trường (missing_fields)
h.missing_fields_table(meta)
```

## 6. Lịch sử phiên bản

Lịch sử phiên bản là đầu vào của bước tải nguồn. Bước T3 chỉ tải những phiên bản đã được metadata xác minh, và bộ kiểm tra handoff báo lỗi nếu có nguồn của một phiên bản không nằm trong `versions` (mục 1.7). Vì vậy `versions_complete` chỉ đúng khi cả ba điều kiện sau thỏa:

1. **Liên tục**: các phiên bản là đúng 1, 2, …, N, không thiếu v1 và không có khoảng trống. Pipeline không bao giờ tự chèn một phiên bản mà arXiv không liệt kê.
2. **Đủ ngày**: mọi phiên bản đều có `submitted_at`.
3. **Không bất thường thứ tự ngày**: xét theo số phiên bản, ngày của phiên bản sau không được sớm hơn phiên bản trước (hàm `version_date_order_issues`); ngày bằng nhau được chấp nhận. Khi có bất thường, dữ liệu gốc **không bị sắp xếp lại**; mô tả bất thường được ghi vào `provenance.version_date_order` để người đọc kiểm tra.

Trung bình mỗi bài có {eval}`mean_versions` phiên bản, nhiều nhất là {eval}`max_versions` phiên bản, và {eval}`pct_multi`% số bài có từ hai phiên bản trở lên ([](#fig-1-3-versions)). Tỷ lệ bài có `versions_complete = true` là {eval}`pct_versions_complete`%. Số bài có bất thường thứ tự ngày là {eval}`n_date_order`. Có {eval}`pct_venue`% số bài ghi `venue`.

```{code-cell} python
:tags: [remove-input]
:label: fig-1-3-versions
:caption: Phân bố số phiên bản mỗi bài
h.fig_versions_hist(meta);
```

## 7. Nguồn gốc dữ liệu (provenance)

Mỗi `Result` metadata ghi `provenance` gồm endpoint, URL request đầy đủ, `metadata_prefix = arXivRaw`, đường dẫn trang tóm tắt `arxiv.org/abs/<id>` và mô tả phương pháp. Nhờ vậy, mọi giá trị trong bảng `papers` (mục 1.7) đều truy ngược được về đúng request đã tạo ra nó.

## 8. Tài liệu tham khảo

- arXiv. *arXiv OAI-PMH Interface*. https://info.arxiv.org/help/oa/index.html
- arXiv. *Terms of Use for arXiv APIs*. https://info.arxiv.org/help/api/tou.html
- Open Archives Initiative. *The Open Archives Initiative Protocol for Metadata Harvesting, Version 2.0*. https://www.openarchives.org/OAI/openarchivesprotocol.html
