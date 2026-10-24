# arXiv Data Science — Milestone 1

Repo mã nguồn cho đồ án Nhập môn Khoa học Dữ liệu: thu thập bài báo arXiv, phân tích nội dung và đối sánh tài liệu tham khảo. Hiện đã triển khai **T1: lập danh sách bài trong dải ID qua API arXiv**, **T2: metadata và lịch sử phiên bản qua OAI-PMH arXivRaw**, **T3: tải và trích xuất source TeX/BibTeX**, và **T4: lấy references có arXiv ID của một bài**. Runner toàn pipeline (T5) chưa triển khai.

## Cấu trúc code

```text
src/
├── arxiv_pipeline/
│   ├── __init__.py
│   ├── contracts.py       # Schema chung: Input, Output và Result
│   ├── config.py          # Dải ID được giao cho năm thành viên
│   ├── identifiers.py     # Kiểm tra và chuẩn hóa arXiv ID dùng chung cho T2, T4
│   ├── examples.py        # Tạo dữ liệu mẫu offline theo schema
│   ├── sources/           # T3 của Khang
│   │   ├── __init__.py    # Xuất SourcesModule
│   │   ├── service.py     # fetch_sources: kiểm tra, staging lifecycle, Result
│   │   ├── extractor.py   # Format sniffing, to_relative_path, Strategy handlers, limits
│   │   └── api.py         # SourceClient Protocol, ArxivSourceClient streaming
│   ├── references/        # T4 của Mẫn Nhi
│   │   ├── __init__.py    # Xuất ReferencesModule
│   │   ├── __main__.py    # Điểm chạy python -m arxiv_pipeline.references
│   │   ├── cli.py         # Chạy một bài, ghi preview JSON
│   │   ├── service.py     # fetch_references: phân trang, gộp trùng, status, metrics
│   │   ├── api.py         # Client Semantic Scholar + client ngày nộp arXiv, parser
│   │   └── examples.py    # Bốn Result mẫu giả lập sinh bằng chính module
│   ├── discovery/         # T1 của Trọng
│   │   ├── __init__.py    # Các hàm dùng khi tích hợp T1
│   │   ├── __main__.py    # Điểm chạy python -m arxiv_pipeline.discovery
│   │   ├── cli.py         # Đọc tham số, gọi T1 và ghi preview JSON
│   │   ├── service.py     # Sinh ID ứng viên, xác minh và tổng hợp Result
│   │   └── api.py         # Gửi request, retry và đọc Atom XML
│   └── metadata/          # T2 của Huỳnh Nhi
│       ├── __init__.py    # MetadataModule dùng khi tích hợp T2
│       ├── __main__.py    # Điểm chạy python -m arxiv_pipeline.metadata
│       ├── cli.py         # Đọc tham số, gọi T2 và ghi preview JSON
│       ├── service.py     # Kiểm tra input, chọn status, tính missing_fields
│       └── api.py         # Client OAI-PMH và đọc arXivRaw XML
└── requirements.txt
tests/
├── fixtures/metadata/     # Phản hồi arXivRaw giả lập cho T2
├── test_discovery.py
├── test_references.py
├── test_metadata.py
├── test_identifiers.py
└── test_contract_examples.py
```

Để đọc T1, bắt đầu từ `contracts.py` để hiểu dữ liệu, rồi xem `config.py` để biết dải ID. Tiếp theo đọc `cli.py` để thấy cách chạy, `service.py` để hiểu luồng xử lý và `api.py` để xem cách giao tiếp với arXiv. `examples.py` dùng khi các thành viên cần đầu ra giả lập để phát triển task của mình.

Luồng gọi khi chạy CLI:

```text
__main__.py → cli.py → service.py → api.py → API arXiv
                 ↑         │
                 └─ Result ┘
                 │
                 └→ preview/<run_id>/<owner>.json
```

Schema và cấu hình nằm ngoài `discovery` vì các task khác cũng cần dùng. Khi triển khai T2–T4, mỗi task sẽ có module riêng trong `arxiv_pipeline`.

## Chạy T1

Dùng Python 3.10 trở lên. T1 và test offline chỉ dùng thư viện chuẩn, không cần cài thêm package. Chạy các lệnh sau từ thư mục `arXiv`.

```bash
PYTHONPATH=src python -m arxiv_pipeline.discovery \
  --owner trong \
  --run-id t1-trong-demo \
  --output-root /tmp/arxiv-t1
```

Mặc định chỉ xác nhận **10 ID ứng viên đầu** trong dải của người được chọn. Có thể chọn `trong`, `khang`, `hoang`, `man-nhi` hoặc `huynh-nhi`. Muốn đổi số ứng viên:

```bash
PYTHONPATH=src python -m arxiv_pipeline.discovery \
  --owner trong --run-id t1-trong-20 \
  --output-root /tmp/arxiv-t1 --max-candidates 20
```

Số ứng viên không phải số bài tìm được. Lượt thử giới hạn phạm vi trả `partial` dù mọi request đã thành công. `--max-candidates` không thay đổi hai mốc của dải được giao.

Kết quả preview:

```text
/tmp/arxiv-t1/preview/t1-trong-demo/trong.json
```

Đây là toàn bộ Result discovery: danh sách `data.papers`, ID chưa xác minh, ID bị loại, trạng thái, provenance, thời gian và số request. `is_mock=false` nghĩa là sử dụng nguồn thật; lượt chạy vẫn có thể thất bại do mạng. Không ghi vào corpus chính thức, SQLite hoặc log của T5.

| Exit code | Kết quả |
|---|---|
| 0 | Duyệt đủ phạm vi, không còn ID chưa xác minh |
| 2 | Có kết quả nhưng chưa hoàn tất: giới hạn chạy thử hoặc còn ID chưa xác minh |
| 1 | Không xác minh được dữ liệu, cấu hình hoặc ghi file lỗi |

Lỗi cú pháp tham số do argparse báo exit 2 kèm hướng dẫn; nó không tạo Result. Xem thêm `PYTHONPATH=src python -m arxiv_pipeline.discovery --help`.

## Chạy toàn dải

Chỉ chạy sau khi kiểm tra tập nhỏ và phối hợp lượt truy cập của nhóm:

```bash
PYTHONPATH=src python -m arxiv_pipeline.discovery \
  --owner trong --run-id t1-trong-full \
  --output-root /tmp/arxiv-t1 --full-range
```

`--full-range` và `--max-candidates` loại trừ nhau. Hai đầu mút được lấy theo `inclusive=true` trong cấu hình hiện có.

Với dải qua tháng, chương trình duyệt đến **99.999** ở tháng đầu rồi bắt đầu từ 1 ở tháng kế tiếp. Đây là giới hạn không gian số của ID hiện đại, không phải số bài thực tế trong tháng. Cách này không cần đoán điểm cuối tháng; đổi lại có nhiều ứng viên không tồn tại cần xác nhận. Dải Trọng có **83.142 ứng viên**, tương đương ít nhất **832 request theo lô**, chưa gồm retry hoặc kiểm tra riêng. CLI in chi phí theo lô trước khi gửi. Không dừng ở ID trống đầu tiên vì như vậy có thể bỏ sót bài.

T1 hỗ trợ cấu hình ID năm 2015–2099, phù hợp năm dải hiện tại. Chưa có checkpoint/resume hoặc cache bền vững; mỗi lần gọi sẽ kiểm tra lại phạm vi. Preview chỉ được xuất khi hàm trả về, nên không dùng nó thay cơ chế phục hồi T5. Cùng run_id/owner sẽ ghi đè preview sau khi có Result mới; dùng run_id mới nếu cần giữ lịch sử thử.

## Dùng từ Python

```python
from pathlib import Path
from arxiv_pipeline.config import ASSIGNED_RANGES
from arxiv_pipeline.discovery import DiscoveryModule
from arxiv_pipeline.discovery.api import ApiClient

config = next(dict(c) for c in ASSIGNED_RANGES if c["owner"] == "trong")
context = {
    "run_id": "t1-demo",
    "is_mock": False,
    "output_root": Path("/tmp/arxiv-t1"),
}
client = ApiClient()
module = DiscoveryModule(client, max_candidates=10)
result = module.discover(config, context)
# Runner dùng result["data"]["papers"] nếu có data; không truy cập data khi failed.
```

Hàm không tự ghi file; CLI là wrapper xuất preview. Khi tích hợp, thay client bằng client dùng chung đáp ứng `query(ids) -> bytes` và bộ đếm `attempts`. Test có thể dùng client giả trả Atom bytes, không cần truy cập mạng. Dùng một instance client dùng chung và chạy các lời gọi discovery lần lượt; nhiều máy/process phải phối hợp bên ngoài.

## Chạy T4 (references)

`fetch_references(task, context)` nhận một PaperTask và trả một Result `step=references`. Danh sách reference lấy từ Semantic Scholar Graph API (phân trang theo `next`); **ngày nộp lấy từ API arXiv** (`published` của v1), không bao giờ lấy `publicationDate` của Semantic Scholar hay suy từ ID. Chỉ dùng thư viện chuẩn.

```bash
# S2_API_KEY là tùy chọn (tăng hạn mức); không ghi key vào mã nguồn.
PYTHONPATH=src python -m arxiv_pipeline.references --owner man-nhi --arxiv-id 2501.12642 --run-id t4-demo --output-root /tmp/arxiv-t4
```

Trên Windows (PowerShell, dùng `py` thay cho `python`; `PYTHONPATH` đặt bằng `$env:`):

```powershell
$env:PYTHONPATH = "src"
py -m arxiv_pipeline.references --owner man-nhi --arxiv-id 2501.12642 --run-id t4-demo --output-root .\preview-t4
```

Preview nằm ở `/tmp/arxiv-t4/preview/t4-demo/man-nhi-2501-12642.references.json` (PowerShell: `.\preview-t4\preview\t4-demo\...`); không ghi vào corpus chính thức. Exit code: 0 = success/empty, 2 = partial, 1 = failed/not_found hoặc tham số/ghi file lỗi.

```python
from arxiv_pipeline.references import ReferencesModule
from arxiv_pipeline.references.api import ArxivDateClient, SemanticScholarClient

module = ReferencesModule(SemanticScholarClient(), ArxivDateClient())
result = module.fetch_references({"owner": "man-nhi", "arxiv_id": "2501.12642"}, context)
```

`--owner` là bắt buộc (owner lấy từ task/cấu hình, không đặt mặc định). Client được truyền khi khởi tạo; thay bằng adapter/cache dùng chung của runner nếu đáp ứng `references_page(arxiv_id, offset, limit) -> dict` và `submission_dates(ids) -> dict[id, str]` (kèm bộ đếm `attempts`).

| Tình huống | status | `data.complete` | Ghi chú |
|---|---|---|---|
| Hết trang, có reference, mọi mục đủ title/authors/submission_date/semantic_scholar_id | success | true | `missing_fields=[]`, `error=null` |
| Hết trang, thiếu ít nhất một trường của một mục (ví dụ không tra được ngày) | partial | **true** | `complete` chỉ nghĩa là đã lấy hết trang; trường thiếu nằm trong `missing_fields` |
| Hết trang, không có reference arXiv nào (kể cả danh sách rỗng) | empty | true | `error=null` |
| Trang sau trang đầu bị lỗi | partial | false | giữ phần đã nhận, `error` ghi nguyên nhân |
| Chạm giới hạn số trang (`max_pages`, mặc định 50) khi danh sách chưa hết | partial | false | `error.code=remote_error`, `retryable=false`, thông điệp nêu giới hạn trang; spec chưa có mã riêng cho trường hợp này |
| Lỗi ở trang đầu | failed | — | `data=null`, `metrics={}` |
| Semantic Scholar trả 404 | not_found | — | `data=null`, `retryable=false` |

Metrics bắt buộc (khi có `data`): `s2_reference_count` (mọi mục nhận được, trước lọc/trùng), `arxiv_reference_count` (ID arXiv duy nhất giữ lại, bằng số key của `references`), `complete_reference_count` (đủ bốn trường); luôn có `complete <= arxiv <= s2`. Hoàng tính `reference_metadata_success_rate` và `mean_arxiv_references_per_paper` từ các số này và cờ `complete`.

T2 và T4 dùng chung `identifiers.py` để kiểm tra ID: tháng 01–12, số thứ tự từ 1, đúng số chữ số theo giai đoạn. PaperTask chỉ nhận ID chuẩn, không nhận URL, tiền tố, khoảng trắng hoặc hậu tố phiên bản; ID sai bị từ chối trước khi gửi request.

Quy tắc dữ liệu: ID reference từ nguồn ngoài được bỏ khoảng trắng hai đầu, tiền tố `arXiv:` và hậu tố `vN`; dạng cũ có phân lớp như `math.GT/0309136v2` được chuẩn hóa thành `math/0309136`. ID không hợp lệ bị bỏ trước khi tra ngày, vẫn được tính trong `s2_reference_count`. ID cũ giữ dấu `/` (`hep-th/9901001`); khử trùng theo ID chuẩn, mỗi trường lấy giá trị không thiếu đầu tiên, không trộn danh sách tác giả; trùng/xung đột ghi trong `provenance` theo JSON Pointer của trường (`/data/references/<id>/<field>`, `~` → `~0`, `/` → `~1`). Tên tác giả/tiêu đề rỗng được coi là thiếu (`null`). Ngày do adapter trả về luôn được kiểm tra lại: chỉ nhận ID thuộc batch đã hỏi và chuỗi ISO hợp lệ (ngày, hoặc timestamp có múi giờ); giá trị khác (`"2020"`, `"N/A"`) coi là thiếu và vào `missing_fields`. Task hoặc context sai kiểu ném `ValueError` trước khi gửi request; lỗi lập trình không bị đổi thành references rỗng.

Mẫu offline (đủ metadata, thiếu ngày, rỗng, lỗi phân trang), do chính module sinh từ reader giả lập, `is_mock=true`:

```bash
PYTHONPATH=src python -m arxiv_pipeline.references.examples --owner man-nhi --output-dir /tmp/arxiv-t4-examples
```

Test T4 (`tests/test_references.py`) không gửi request thật. Đã kiểm chứng thêm với Semantic Scholar/arXiv thật:

| Bài | Kết quả | Ghi chú |
|---|---|---|
| `1706.03762` | `success`; `s2_reference_count=41`, `arxiv_reference_count=30`, `complete_reference_count=30` | Số lượng khớp truy vấn trực tiếp Semantic Scholar; ngày nộp của 30 bài cùng tháng với ID arXiv |
| `2501.12642` | `empty`; 0/0/0 | Semantic Scholar trả danh sách reference rỗng (truy vấn trực tiếp cũng ra 0 mục); `empty` chỉ nghĩa là nguồn không có dữ liệu, không khẳng định bài không trích dẫn ai |
| `1706.03762v7` | từ chối trước khi gửi request | ID có hậu tố phiên bản |

Chưa kiểm chứng bằng dữ liệu thật: reference có ID cũ dạng `hep-th/9901001`. arXiv đôi khi giới hạn tốc độ (429); khi đó client thử lại tối đa 3 lần và bài ra `partial` với `error.code=rate_limited`, chạy lại sau ít phút.

## Cách xác nhận ID và xử lý lỗi (T1)

- Gửi tối đa 100 ID/lô qua `id_list`, với `start=0` và `max_results` bằng số ID của lô.
- Đọc Atom XML và chuẩn hóa URL, bỏ hậu tố `vN`, khử trùng lặp rồi sắp ID. Không giữ metadata chi tiết hoặc lịch sử phiên bản; đó là T2.
- Chỉ loại ID vắng mặt khi phản hồi Atom đã kiểm tra hợp lệ, không có entry lỗi/ngoài lô và pagination xác nhận đầy đủ. Đây là kết quả không tìm thấy tại API ở thời điểm truy vấn, không khẳng định bài chưa từng tồn tại.
- Phản hồi không đầy đủ hoặc payload bất thường được kiểm tra riêng cho ID còn mơ hồ. Nếu vẫn không xác minh được, giữ unresolved; không loại vì timeout hay HTTP 404 của request.
- Một kết nối, chờ ít nhất 3 giây sau mỗi request; timeout 30 giây; tối đa 3 lần gửi. Retry lỗi mạng, 429 và server 500/502/503/504 với backoff 3/6 giây, tôn trọng `Retry-After` cả dạng số giây và ngày HTTP.
- `attempts` đếm request thực tế kể cả retry. Error đầu tiên còn chưa giải quyết được ghi trong Result; tất cả ID chưa xác minh nằm trong data khi partial.
- HTTP 200 không tự được xem là thành công: HTML, XML lỗi, entry lỗi và ID ngoài yêu cầu đều phải kiểm tra. Client không cache phản hồi.

## Chạy T2

T2 nhận một PaperTask (`owner`, `arxiv_id`) và trả Result `step=metadata`: tiêu đề, tác giả, ngày nộp v1, ngày từng phiên bản, venue. Chỉ dùng thư viện chuẩn. Chạy từ thư mục gốc repo.

```bash
PYTHONPATH=src python -m arxiv_pipeline.metadata \
  --owner huynh-nhi \
  --arxiv-id 2503.06914 \
  --run-id t2-demo \
  --output-root /tmp/arxiv-t2
```

Mặc định `--run-id t2-preview`, `--output-root /tmp/arxiv-t2`. Chạy thử không cần mạng bằng một phản hồi đã lưu (`is_mock=true`, không gửi request):

```bash
PYTHONPATH=src python -m arxiv_pipeline.metadata \
  --owner huynh-nhi --arxiv-id 2503.06914 \
  --fixture tests/fixtures/metadata/multiple_versions.xml
```

Kết quả preview:

```text
/tmp/arxiv-t2/preview/t2-demo/metadata/2503-06914.json
```

Đây là toàn bộ Result metadata. Tên file theo quy ước thư mục bài (`.` → `-`, `/` → `__`). Không ghi vào corpus chính thức, SQLite hoặc log của T5.

| Exit code | Kết quả |
|---|---|
| 0 | `success`: đủ title, authors, ngày v1 và lịch sử phiên bản liên tục có ngày |
| 2 | `partial`: có dữ liệu nhưng thiếu trường bắt buộc; xem `missing_fields` |
| 3 | `not_found`: arXiv OAI xác nhận không có bản ghi (hoặc đã xóa) |
| 1 | `failed`, ID/owner sai, hoặc ghi file lỗi |

Lỗi cú pháp tham số do argparse báo exit 2 kèm hướng dẫn; nó không tạo Result.

### Vì sao dùng OAI-PMH arXivRaw

API Atom mà T1 dùng chỉ có ngày v1 (`published`), ngày của bản mới nhất (`updated`) và số phiên bản mới nhất, nên không chứng minh được ngày của các bản ở giữa. Bản ghi arXivRaw liệt kê từng phiên bản kèm ngày, cùng tiêu đề, tác giả và `journal-ref`, trong một request mỗi bài:

```text
https://oaipmh.arxiv.org/oai?verb=GetRecord&metadataPrefix=arXivRaw&identifier=oai:arXiv.org:<id>
```

Kiểm tra lại địa chỉ này trước lượt chạy thật (arXiv đã chuyển OAI từ `export.arxiv.org/oai2`); nếu đổi thì sửa hằng `OAI_URL` trong `metadata/api.py`.

### Quy tắc dữ liệu và xử lý lỗi

- ID được kiểm tra trước khi gửi request: tháng 01–12, số thứ tự từ 1, 4 chữ số cho 0704–1412 và 5 chữ số từ 1501; ID kiểu cũ `archive/YYMMNNN` từ 9108 đến 0703 (không kèm phân lớp như `math.GT`). Không nhận URL hay hậu tố `vN`.
- `OaiClient` kế thừa `ApiClient` của T1: một kết nối, chờ ít nhất 3 giây sau mỗi request, timeout 30 giây, tối đa 3 lần gửi, retry lỗi mạng, 429 và 500/502/503/504, tôn trọng `Retry-After`. Mã lỗi giống T1: chỉ 429 là `rate_limited`; 503 (OAI báo chờ) là `remote_error` có retry. Không cache.
- `not_found` chỉ khi OAI trả `idDoesNotExist` hoặc bản ghi `deleted`. Lỗi mạng, HTTP 404 hoặc lỗi OAI khác là `failed`. Bài vừa nộp trong ngày có thể chưa có trên OAI; chạy lại sau.
- HTTP 200 không tự được xem là thành công: XML rỗng/hỏng, có DTD, trang HTML, ID không khớp, trường bị lặp (`title`, `authors`, `id`, ngày của một phiên bản, nhiều record) đều là `invalid_payload`.
- Phiên bản: giữ mọi phiên bản arXivRaw liệt kê, sắp theo số; không chèn phiên bản không có. Thiếu ngày, thiếu v1 hoặc có khoảng trống → `partial` và ghi JSON Pointer tương ứng vào `missing_fields`. `submission_date` là ngày v1; `revised_dates` là ngày đã biết của v2 trở đi theo thứ tự phiên bản. Ngày không có múi giờ không được đoán (để `null`).
- Thứ tự ngày phiên bản: so sánh các thời điểm đã biết theo số version sau khi chuẩn hóa múi giờ; ngày bằng nhau hợp lệ. Nếu ngày của một phiên bản sớm hơn ngày đã biết của phiên bản trước, giữ nguyên dữ liệu nguồn, trả `partial`, đặt `versions_complete=false` và ghi cặp phiên bản cùng giá trị ngày của chúng vào `provenance.version_date_order`. Bỏ qua ngày `null` khi so sánh nhưng vẫn kiểm tra các ngày đã biết ở hai phía. Ngày còn giá trị không được đưa vào `missing_fields`; bất thường thứ tự ngày không tạo lỗi HTTP nên `error=null`.
- Tác giả: theo [quy ước nộp bài của arXiv](https://info.arxiv.org/help/prep.html), tách theo dấu phẩy và ` and ` ở ngoài ngoặc, bỏ đơn vị trong ngoặc ở mọi độ sâu, giữ `Jr.`/`Sr.`/`II`/`III` với tên trước. Nhóm cộng tác (`ABCD Collaboration: ...`, `..., for the ABCD Collaboration`) thành một mục riêng `ABCD Collaboration`. Dấu TeX (`M\"uller`) giữ nguyên như nguồn. Ngoặc lệch, danh sách có `et al.`, hoặc có `;`/`:` không thuộc mẫu nhóm cộng tác → `authors=null`, `partial`.
- `provenance` ghi nguồn thật: URL OAI và trang abstract `https://arxiv.org/abs/<id>` khi gọi arXiv, tên file khi dùng `--fixture`.
- Theo [điều khoản truy cập](https://info.arxiv.org/help/api/tou.html): tối đa 1 request mỗi 3 giây và một kết nối cho **mọi máy của nhóm cộng lại**; không chạy song song T1 và T2 trên nhiều máy cùng lúc. Được lưu và chia sẻ metadata mô tả; không ghi dự án là được arXiv bảo trợ.
- `attempts` đếm request thực tế kể cả retry; dùng file đã lưu thì bằng 0. `metrics` luôn `{}`.

### Dùng từ Python

```python
from pathlib import Path
from arxiv_pipeline.metadata import MetadataModule
from arxiv_pipeline.metadata.api import OaiClient

context = {"run_id": "t2-demo", "is_mock": False, "output_root": Path("/tmp/arxiv-t2")}
module = MetadataModule(OaiClient())
result = module.fetch_metadata({"owner": "huynh-nhi", "arxiv_id": "2503.06914"}, context)
# Runner tải các phiên bản trong result["data"]["versions"] khi data khác None, kể cả partial.
```

Hàm không tự ghi file. Client dùng chung cần `record(arxiv_id) -> bytes`, `provenance(arxiv_id) -> dict[str, str]` và bộ đếm `attempts`. Mỗi bài một request nên 1.000 bài mất khoảng 50 phút; chưa có checkpoint/resume.

## T3 — Tải và trích xuất nguồn TeX/BibTeX (SourcesModule)

T3 chịu trách nhiệm tải gói source của một phiên bản arXiv, giải nén và chỉ giữ lại các file `.tex` và `.bib` nguyên bản, loại bỏ hoàn toàn các file hình ảnh/binary và các file phụ trợ (`.sty`, `.cls`, `.bbl`), phục vụ bàn giao sang T5.

### Dùng từ Python

```python
from pathlib import Path
from arxiv_pipeline.sources import SourcesModule
from arxiv_pipeline.sources.api import ArxivSourceClient
from arxiv_pipeline.sources.extractor import SourceExtractor

client = ArxivSourceClient()
extractor = SourceExtractor()
module = SourcesModule(client, extractor)

context = {"run_id": "t3-demo", "is_mock": False, "output_root": Path("/tmp/arxiv-t3")}
task = {"owner": "khang", "arxiv_id": "2501.12642"}
result = module.fetch_sources(task, version=1, context=context)
```

### Quy tắc dữ liệu và bàn giao T5

- **Phạm vi file giữ lại:** Chỉ giữ lại `.tex` và `.bib`. Nếu archive không chứa bất kỳ file `.tex` nào (ví dụ chỉ có file `.bib` hoặc chỉ có hình ảnh), trạng thái trả về là `unsupported` và không có file nào được chấp nhận (`data.files=[]`, `retained_source_bytes=0`).
- **Vị trí lưu trữ (Staging):** Khi trích xuất thành công, file nguồn được lưu cô lập tại `output_root/staging/<run_id>/<owner>/<paper_folder>/v<version>`. Đường dẫn `data.source_dir` luôn mang định dạng tương lai `raw/<owner>/<paper_folder>/v<version>`, T3 không ghi trực tiếp vào `raw/`. T5 sẽ sở hữu việc kiểm tra và chuyển tiếp vào `raw/`.
- **Đo lường byte metrics:** Thống kê 5 chỉ số byte: `download_bytes`, `extracted_bytes_before_filter`, `retained_source_bytes`, `removed_figure_bytes`, `removed_other_bytes` với bất biến bảo toàn byte `before == retained + removed_figure + removed_other`.
- **An toàn archive:** Chặn toàn bộ path traversal (`..`), đường dẫn tuyệt đối, symlink, hardlink, file đặc biệt và các member trùng lặp. Khi xảy ra lỗi giữa chừng, toàn bộ dữ liệu tạm thời được dọn dẹp sạch sẽ (rollback).
- **Kiểm soát request:** Endpoint `https://export.arxiv.org/src/<id>v<version>`, tải dạng luồng (streaming), tuân thủ `Retry-After`, pacing tối thiểu 3 giây giữa các request và kiểm soát dung lượng tải tối đa.

## Kiểm thử offline

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Test không gửi request thật và dùng thư mục tạm. T1 gồm dải qua tháng, đầu mút, giới hạn thử, ID trống, phiên bản/trùng thứ tự, Atom lỗi, phân trang thiếu, HTTP retry và CLI. T2 gồm một/nhiều phiên bản, thiếu ngày, thiếu v1, khoảng trống, not_found, bản ghi bị xóa, lỗi mạng/HTTP, payload lặp hoặc hỏng, ID không hợp lệ, tác giả có đơn vị lồng nhau và CLI với các file trong `tests/fixtures/metadata/`.

## Schema và tài liệu

Cả nhóm dùng **schema 1.1** theo tài liệu bên dưới. `SCHEMA_VERSION` trong `src/arxiv_pipeline/contracts.py` là hằng phiên bản dùng chung; T1 và generator cùng import hằng này. File contracts cũng mô tả kiểu dữ liệu của từng task, kiểm tra và thống kê; TypedDict không tự kiểm tra dữ liệu lúc chạy. Bộ validator tổng quát chưa triển khai.

Tạo dữ liệu mẫu offline bằng thư mục mới hoặc rỗng:

```bash
PYTHONPATH=src python -m arxiv_pipeline.examples --output-dir /tmp/arxiv-m1-examples-v11
```

Generator tạo Result mẫu cho năm thành viên, archive thử, cả thư mục staging và corpus mô phỏng, cùng scope/accepted_results cho phần thống kê. Nó mô phỏng đầu ra bàn giao, không phải runner thật hoặc crawler các task còn lại. Các mẫu đều `is_mock=true`, request thực tế bằng 0; lỗi 429/404 trong edge_cases là tình huống giả lập. Không đưa mẫu vào báo cáo dữ liệu thật. Mẫu tạo trước khi nâng cấp cần tạo lại, không đổi nhãn phiên bản trên file cũ.

- [Schema 1.1 và phân công](docs/superpowers/specs/2026-10-16-milestone-1-schema-and-tasks.md)
- [Kế hoạch theo tuần](docs/superpowers/plans/2026-10-14-milestone-1-team-plan.md)
- [Đề bài milestone 1](context/crawling.md)
- [Hướng dẫn API arXiv](https://info.arxiv.org/help/api/user-manual.html)
- [Điều khoản truy cập API](https://info.arxiv.org/help/api/tou.html)

Mã nguồn không tự commit, push hoặc xuất bản dữ liệu.
