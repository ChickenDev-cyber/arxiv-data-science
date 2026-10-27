# arXiv Data Science — Milestone 1

Repo mã nguồn cho đồ án Nhập môn Khoa học Dữ liệu: thu thập bài báo arXiv, phân tích nội dung và đối sánh tài liệu tham khảo. Đã có T1 discovery, T2 metadata/lịch sử phiên bản, T3 nguồn TeX/BibTeX, T4 references và **T5 runner theo công đoạn, có SQLite và checkpoint discovery theo lô**. Độ phủ và hiệu năng toàn dải phải được đo bằng lượt chạy thật; các phần tích hợp còn chờ được nêu ở mục T5.

## Cấu trúc code

```text
src/
├── arxiv_pipeline/
│   ├── __init__.py
│   ├── contracts.py       # Schema chung: Input, Output và Result
│   ├── pipeline_contracts.py # Kiểu checkpoint/snapshot/collection và Protocol
│   ├── json_utils.py      # JSON canonical dùng chung
│   ├── config.py          # Dải ID được giao cho năm thành viên
│   ├── identifiers.py     # Kiểm tra và chuẩn hóa arXiv ID dùng chung cho T2, T4
│   ├── examples.py        # Tạo dữ liệu mẫu offline theo schema
│   ├── runner/            # PipelineRunner, factory, stage, SQLite, checkpoint, relocate
│   ├── validation/        # Kiểm tra Result, scope và corpus
│   ├── sources/           # T3 của Khang
│   │   ├── __init__.py    # Xuất SourcesModule
│   │   ├── service.py     # fetch_sources: kiểm tra, staging lifecycle, Result
│   │   ├── extractor.py   # Format sniffing, to_relative_path, Strategy handlers, limits
│   │   └── api.py         # SourceClient Protocol, ArxivSourceClient streaming
│   ├── references/        # T4 của Mẫn Nhi
│   │   ├── __init__.py    # Xuất ReferencesModule
│   │   ├── __main__.py    # Điểm chạy python -m arxiv_pipeline.references
│   │   ├── cli.py         # Chạy một bài, ghi preview JSON
│   │   ├── service.py     # fetch_references: giữ raw, phân trang, gộp trùng, status, metrics
│   │   ├── api.py         # Client Semantic Scholar + client ngày nộp arXiv, parser
│   │   ├── checkpoint.py  # PageCheckpoint, DateCache, store bộ nhớ/file JSON
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
├── test_references_resume.py
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

Schema và cấu hình nằm ngoài `discovery` để các module dùng chung. Các service nhận client/port qua constructor; runner điều phối, công nhận dữ liệu và ghi trạng thái.

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

T1 hỗ trợ cấu hình ID năm 2015–2099, phù hợp năm dải hiện tại. CLI T1 độc lập chỉ ghi preview sau khi hàm trả về và không tự lưu checkpoint. Muốn tiếp tục discovery theo lô, dùng runner T5 bên dưới: các lô đã xác minh được lưu trong SQLite, lô dở chỉ truy vấn lại ID unresolved. Python API nhận port tùy chọn `DiscoveryModule(client, checkpoints=store)`; store cần được ràng buộc vào run tương ứng. Cùng run_id/owner ở CLI preview sẽ ghi đè preview sau Result mới.

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

Theo đề mới, schema **1.2** giữ cả hai phần trong `Result.data`:

| Trường | Nội dung |
|---|---|
| `raw_references` | Danh sách các object trong `data` của những trang Semantic Scholar đã chấp nhận; giữ mọi trường, giá trị gốc, thứ tự và duplicate, kể cả mục không có arXiv ID hoặc `citedPaper=null` |
| `references` | Dictionary có arXiv ID hợp lệ, đã chuẩn hóa/khử trùng và bổ sung metadata |
| `complete` | Đã lấy hết trang Semantic Scholar hay chưa |

Ví dụ nhận 15 mục, sau chuẩn hóa có 10 ID arXiv phân biệt: `len(raw_references)=15`, `len(references)=10`. Bản raw được sao chép ngay khi nhận từng trang, không bị thay đổi khi reader tái sử dụng dữ liệu hoặc khi chuẩn hóa tập arXiv. Trang lỗi cấu trúc bị từ chối nguyên trang; nếu trang sau lỗi, giữ các trang đã chấp nhận và trả `partial`, `complete=false`. Nếu ngay trang đầu lỗi, `data=null` như trước.

T4 chỉ trả Result và preview. T5 lưu dictionary vào `references.json` và danh sách gốc vào `raw_references.json`, đồng thời giữ status/complete trong log và SQLite. Bộ mẫu chung mô phỏng cả hai file; có thể thử writer thật bằng CLI runner ở phần T5. Chỉ tập arXiv được xuất thành các hàng của bảng `references` cuối đồ án.

| Tình huống | status | `data.complete` | Ghi chú |
|---|---|---|---|
| Hết trang, có reference arXiv, mọi mục arXiv đủ title/authors/submission_date/semantic_scholar_id | success | true | `missing_fields=[]`, `error=null` |
| Hết trang, thiếu ít nhất một trường của một mục arXiv (ví dụ không tra được ngày) | partial | **true** | `complete` chỉ nghĩa là đã lấy hết trang; trường thiếu nằm trong `missing_fields` |
| Hết trang, không có reference arXiv nào | empty | true | `raw_references` vẫn có thể không rỗng; `error=null` |
| Trang sau trang đầu bị lỗi | partial | false | giữ phần đã nhận, `error` ghi nguyên nhân |
| Chạm giới hạn số trang (`max_pages`, mặc định 50) khi danh sách chưa hết | partial | false | `error.code=remote_error`, `retryable=false`, thông điệp nêu giới hạn trang; spec chưa có mã riêng cho trường hợp này |
| Lỗi ở trang đầu | failed | — | `data=null`, `metrics={}` |
| Semantic Scholar trả 404 | not_found | — | `data=null`, `retryable=false` |

Metrics bắt buộc (khi có `data`): `s2_reference_count` (mọi mục nhận được trước lọc/trùng, bằng `len(raw_references)`), `arxiv_reference_count` (ID arXiv duy nhất giữ lại, bằng số key của `references`), `complete_reference_count` (mục arXiv đủ bốn trường); luôn có `complete <= arxiv <= s2`. Status và missing_fields đánh giá tập arXiv; mục raw thiếu arXiv ID không tự trở thành lỗi thiếu trường. Hoàng tính `reference_metadata_success_rate` và `mean_arxiv_references_per_paper` từ các số này và cờ `complete`.

T2 và T4 dùng chung `identifiers.py` để kiểm tra ID: tháng 01–12, số thứ tự từ 1, đúng số chữ số theo giai đoạn. PaperTask chỉ nhận ID chuẩn, không nhận URL, tiền tố, khoảng trắng hoặc hậu tố phiên bản; ID sai bị từ chối trước khi gửi request.

Quy tắc dữ liệu: ID reference từ nguồn ngoài được bỏ khoảng trắng hai đầu, tiền tố `arXiv:` và hậu tố `vN`; dạng cũ có phân lớp như `math.GT/0309136v2` được chuẩn hóa thành `math/0309136`. ID không hợp lệ bị bỏ trước khi tra ngày, vẫn được tính trong `s2_reference_count`. ID cũ giữ dấu `/` (`hep-th/9901001`); khử trùng theo ID chuẩn, mỗi trường lấy giá trị không thiếu đầu tiên, không trộn danh sách tác giả; trùng/xung đột ghi trong `provenance` theo JSON Pointer của trường (`/data/references/<id>/<field>`, `~` → `~0`, `/` → `~1`). Tên tác giả/tiêu đề rỗng được coi là thiếu (`null`). Ngày do adapter trả về luôn được kiểm tra lại: chỉ nhận ID thuộc batch đã hỏi và chuỗi ISO hợp lệ (ngày, hoặc timestamp có múi giờ); giá trị khác (`"2020"`, `"N/A"`) coi là thiếu và vào `missing_fields`. Task hoặc context sai kiểu ném `ValueError` trước khi gửi request; lỗi lập trình không bị đổi thành references rỗng.

Mẫu offline (đủ metadata, thiếu ngày, rỗng, lỗi phân trang), do chính module sinh từ reader giả lập, `is_mock=true`:

```bash
PYTHONPATH=src python -m arxiv_pipeline.references.examples --owner man-nhi --output-dir /tmp/arxiv-t4-examples
```

### T4 resume: tiếp tục theo trang, cache ngày nộp, lịch chờ API

T4 dùng `CheckpointStore` chung trong `pipeline_contracts.py` (`load(namespace, key)`, `save(record)`, `delete(namespace, key)`). `LiveModuleFactory` truyền cùng store cho `ReferencesModule(checkpoints=...)` và `RequestScheduler(checkpoints=...)`; `store=` vẫn được giữ làm bí danh tương thích ngược. Khi dùng runner, dữ liệu checkpoint nằm trong SQLite. CLI T4 độc lập hỗ trợ `MemoryCheckpointStore` cho test và `JsonFileCheckpointStore` cho state bền vững.

| Namespace | Key (chuỗi JSON chuẩn tắc) | Payload theo hợp đồng chung |
|---|---|---|
| `references` | `["<arxiv_id>"]` | `owner`, `arxiv_id`, `page_size`, `fields`, `pages[]`; mỗi trang có `offset`, `next_offset`, `raw_items`, `fetched_at` |
| `arxiv_dates` | `["<cited_arxiv_id>"]` | `arxiv_id`, `submission_date`, `source`, `fetched_at` |
| `rate_limits` | `["arxiv"]` hoặc `["semantic_scholar"]` | `group`, `next_allowed_at` (UTC ISO 8601), `recent_requests_at` (danh sách UTC ISO 8601), `consecutive_429` |

- `ReferencesModule(reader, dates, checkpoints=store)` lưu sau mỗi trang được chấp nhận. Khi resume, chỉ nối tiếp nếu `owner`, `arxiv_id`, `page_size` và `fields` khớp; nếu cấu hình khác hoặc checkpoint không hợp lệ, bắt đầu lại từ trang đầu để không trộn dữ liệu từ các truy vấn khác nhau. Checkpoint hoàn tất được dùng lại; không tự hết hạn, muốn lấy mới thì gọi `PageCheckpoint(store).discard(arxiv_id)`.
- Cache ngày chỉ lưu ngày hợp lệ lấy từ arXiv API v1 (`published`), kèm nguồn và thời điểm lấy. ID không được API trả về không bị cache thành “không tồn tại”; batch lỗi chỉ khiến các ID chưa có ngày được hỏi lại.
- Store chung xác thực namespace và key. Key là chuỗi JSON chuẩn tắc biểu diễn mảng không rỗng, ví dụ `["2001.00001"]`. Payload tuân thủ `ReferencesCheckpoint`, `ArxivDateCheckpoint` và `RateLimitCheckpoint` trong `pipeline_contracts.py`.
- Checkpoint hỏng/sai khóa/sai phiên bản bị bỏ qua và chạy lại từ đầu, không làm hỏng Result. Lỗi ghi checkpoint do I/O (`OSError`) không làm mất kết quả đang có; được ghi ở `provenance.checkpoint_write_errors`. `provenance.pages_resumed` và `dates_from_cache` cho biết phần dùng lại.
- Lịch chờ lưu bằng UTC ISO 8601 vì đồng hồ monotonic không sống qua restart; khi khôi phục chuyển lại sang đồng hồ monotonic và giới hạn thời gian chờ khôi phục tối đa 24 giờ. Scheduler lưu cooldown, khoảng cách tối thiểu giữa request, chuỗi 429 và cửa sổ 100 request/300 giây của Semantic Scholar.

Chạy thử riêng lẻ (cần mạng): `PYTHONPATH=src python -m arxiv_pipeline.references --owner man-nhi --arxiv-id 2501.12642 --state-dir /tmp/t4-state`; Ctrl+C rồi chạy lại cùng lệnh sẽ tiếp tục từ trang đang dở. Test offline: `tests/test_references_resume.py`.

Test T4 (`tests/test_references.py`) không gửi request thật. Kiểm thử schema 1.2 bao gồm giữ raw có/không có arXiv ID, duplicate, ID sai, citedPaper=null, phân trang lỗi, reader tái sử dụng object và JSON preview. Các lần kiểm chứng Semantic Scholar/arXiv thật dưới đây có trước cập nhật 1.2; lần cập nhật raw_references này chỉ được kiểm thử offline, không xác nhận lại số liệu trực tiếp:

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
- **Nhận diện nguồn:** Từ chối tài liệu HTML ở đầu nội dung trực tiếp hoặc gzip, không lưu trang lỗi thành `main.tex`. TeX có phần chú thích dài hoặc chứa ví dụ HTML trong nội dung vẫn được giữ nguyên. PDF và gói DOCX thông thường đã xác nhận cấu trúc OOXML, không có `.tex`, trả `unsupported`; DOCX dùng `source_kind=other`. ZIP chưa nhận diện, XML chính hỏng/encoding không hỗ trợ hoặc lỗi CRC của bất kỳ thành viên DOCX nào trả `failed/invalid_source`, không kết luận là không có TeX. Việc kiểm tra DOCX đọc theo từng khối, tuân thủ giới hạn số thành viên, byte mỗi tệp và tổng byte, không trích xuất ZIP vào corpus.
- **Vị trí lưu trữ (Staging):** Khi trích xuất thành công, file nguồn được lưu cô lập tại `output_root/staging/<run_id>/<owner>/<paper_folder>/v<version>`. Đường dẫn `data.source_dir` luôn mang định dạng tương lai `raw/<owner>/<paper_folder>/v<version>`, T3 không ghi trực tiếp vào `raw/`. T5 kiểm tra và chuyển tiếp vào `raw/` qua journal có thể phục hồi.
- **Đo lường byte metrics:** Thống kê 5 chỉ số byte: `download_bytes`, `extracted_bytes_before_filter`, `retained_source_bytes`, `removed_figure_bytes`, `removed_other_bytes` với bất biến bảo toàn byte `before == retained + removed_figure + removed_other`.
- **An toàn archive:** Chặn toàn bộ path traversal (`..`), đường dẫn tuyệt đối, symlink, hardlink, file đặc biệt, member trùng lặp và đường dẫn vừa là file vừa là thư mục (ví dụ `main.tex` và `main.tex/part.tex`). Khi xảy ra lỗi giữa chừng, toàn bộ dữ liệu tạm thời được dọn dẹp sạch sẽ (rollback).
- **Kiểm soát request:** Endpoint `https://export.arxiv.org/src/<id>v<version>`, tải dạng luồng (streaming), tuân thủ `Retry-After`, pacing tối thiểu 3 giây giữa các request và kiểm soát dung lượng tải tối đa. Luồng HTTP bị đứt được retry từ đầu sau khi xóa file dở; hết lượt thì trả Result lỗi mạng. Hạn chờ `Retry-After`, gồm số giây và HTTP-date, vẫn áp dụng sang task tiếp theo trên cùng client khi task trước hết lượt retry. Lịch request arXiv dùng chung giữa các module sẽ do T5 điều phối.

## T5 — Chạy toàn pipeline và chạy tiếp

T5 dùng Python >=3.10 và thư viện chuẩn trên Linux/Colab. Chỉ một runner được ghi vào một output root. Các client arXiv dùng chung lịch request, gồm retry và tra ngày trong T4; Semantic Scholar có lịch riêng. API key tùy chọn lấy từ biến môi trường `S2_API_KEY`.

### Chọn công đoạn

`--stage` có bốn giá trị; mặc định `all`:

| Giá trị | Công việc |
|---|---|
| `discovery` | Xác minh danh sách bài |
| `sources` | Discovery, metadata và nguồn của các phiên bản đã biết |
| `references` | Discovery và references; không cần metadata/nguồn thành công trước |
| `all` | Discovery → metadata/nguồn của mọi bài → references của mọi bài |

Có thể đổi stage khi resume, giữ nguyên run_id/output root và cấu hình đã lưu. Công đoạn nguồn hoàn tất có thể trả mã 2 vì references chưa chạy; xem `validation.json` và `quality.json` để phân biệt tính hợp lệ với độ đầy đủ. Khi dùng lại kết quả references đã có, stage sources không xóa chúng.

Ví dụ chạy hai lượt bằng dữ liệu giả (chọn output root mới):

```bash
PYTHONPATH=src python -m arxiv_pipeline.runner \
  --mock --all-owners --run-id stages-demo \
  --output-root data/stages-demo --stage sources

PYTHONPATH=src python -m arxiv_pipeline.runner \
  --resume --run-id stages-demo --output-root data/stages-demo --stage references
```

### Kiến trúc và API bàn giao

- `pipeline_contracts.py`: giữ Result 1.2, thêm TypedDict checkpoint/snapshot/collection, frozen DTO và các Protocol dùng chung. `format_version=1` là định dạng vận hành, không phải schema crawl.
- `PipelineRunner`: điều phối và phục hồi; `LiveModuleFactory`/`MockModuleFactory` tạo service sau khi store mở. Hàm `run_pipeline(options, modules, ...)` vẫn dùng được với adapter cũ.
- `SQLiteCheckpointStore`: lưu envelope/JSON theo run; có callback `validate_record` cho validator payload dùng chung. `InMemoryCheckpointStore` phục vụ test hoặc chương trình tự quản lý vòng đời cache.
- `StateStore.readonly(path, run_id)`: đọc DB v1/v2 không tạo file hoặc migration; có `read_run_record()` và `artifact_inventory()`. Huỳnh Nhi dùng API này cho inspector. API đọc/restore hiện nhận journal mặc định của runner (rollback/DELETE); DB chế độ WAL bị từ chối trước khi mở để tránh tạo `-wal`/`-shm` hoặc bỏ qua dữ liệu chưa checkpoint.
- `RunStateRelocator.relocate(new_root, run_id, old_root, snapshot_id=...)`: kiểm tra bản sao đã dừng, scope, file/hash và journal, rồi chỉ cập nhật đường dẫn trong config. Khang dùng nó sau khi kiểm tra/unpack snapshot. Đây không phải công cụ đóng gói snapshot; không đổi root bằng cách sửa tay SQLite hoặc Result.

Khi mở DB để ghi, T5 tự migration cấu trúc v1→v2 để thêm bảng run_checkpoints. Accepted data, run_id, fingerprint và schema crawl 1.2 được giữ. DB v2 cần runner mới; code baseline cũ chỉ đọc DB v1. Giữ nguyên đường dẫn cho resume thông thường; khôi phục sang nơi khác đi qua cơ chế snapshot/relocator. Relocator đối chiếu scope với cấu hình đã fingerprint và các giới hạn mẫu trước khi cập nhật root.

**Phần đã có và còn chờ:** checkpoint discovery và adapter lưu trữ đã triển khai. `validation/pipeline_contracts.py`, inspector/collection và snapshot thuộc phần các thành viên đang phát triển. T4 đã tích hợp checkpoint trang references, cache ngày và cooldown qua restart theo hợp đồng chung: `LiveModuleFactory` truyền cùng store cho `ReferencesModule` và `RequestScheduler` qua keyword `checkpoints`; constructor vẫn chấp nhận `store` như bí danh tương thích ngược. Các payload T4 tuân thủ TypedDict trong `pipeline_contracts.py`, với key JSON chuẩn tắc và thời gian ISO 8601. Khi validator chung có mặt, CLI dùng `validate_pipeline_document(..., kind="checkpoint")`; kiểm tra envelope không thay thế kiểm tra đầy đủ payload. Lỗi từ constructor/validator được báo, không âm thầm hạ cấp. Chưa có CLI snapshot/collection hoặc HTTP Range trong đợt T1/T5 này.

CLI ghi `provenance.producer_commit` cho Result mới chỉ khi nhận diện được code pipeline sạch trong Git. Code đang sửa hoặc không có Git để trống thông tin đó; không gán HEAD cho artifacts cũ.

### Chạy thử offline

Chọn một thư mục output mới. Lệnh này gọi các service T1–T4 thật bằng adapter dữ liệu giả, tạo corpus qua writer T5 và không gửi request mạng:

```bash
PYTHONPATH=src python -m arxiv_pipeline.runner \
  --mock --all-owners --run-id t5-demo --output-root data/t5-demo
```

Mẫu mặc định có 5 bài giả lập tại ID đầu dải của mỗi owner, mỗi bài 2 phiên bản nguồn. Có 25 Result được công nhận, 9 mục reference gốc và 4 mục có arXiv ID. Mọi Result có `is_mock=true`; đây là dữ liệu kiểm thử, không dùng để báo cáo corpus thật.

CLI hiện tiến độ trực tiếp trên terminal: bài đang xử lý, bước bắt đầu/kết thúc và task được dùng lại. Khi một bước chạy lâu, khoảng mỗi 15 giây sẽ có dòng “Vẫn đang xử lý” kèm thời gian đã chờ. Các dòng này ghi vào stderr và flush ngay; stdout vẫn chỉ chứa JSON tổng kết. Thêm `--quiet` để tắt tiến độ, các file log và báo cáo vẫn được ghi bình thường. Tính năng hiển thị áp dụng khi khởi động CLI mới; lượt đã chạy từ trước tiếp tục dùng code đã nạp.

Mỗi công đoạn có thanh tiến độ riêng: `Discovery` đếm ID đã xử lý trong giới hạn chạy; `Sources` đếm bài sau metadata và mọi phiên bản nguồn; `References` đếm bài sau bước references. Ví dụ: `Sources [##########----------] 50% | 5/10 bài`. Khi resume, kết quả được dùng lại cũng tăng tiến độ. **100% nghĩa là đã xử lý hết công đoạn**, kể cả kết quả thiếu/thất bại; độ đầy đủ xem tổng kết và quality report. Công đoạn không có bài hiển thị `0/0`, không tính 100%. Terminal tương tác cập nhật một dòng, còn khi chuyển hướng stderr vào file thì ghi các mốc thành dòng thường, không có mã điều khiển terminal. Adapter discovery cũ chưa hỗ trợ callback đếm theo dải thay cho ID.

Sau khi xử lý hết các bài của một thành viên, terminal in số bài thành công, còn thiếu và thất bại; cuối lượt có tổng kết toàn bộ mẫu. Ba nhóm không trùng: thành công nghĩa là metadata đầy đủ, nguồn của mọi phiên bản thành công và references success/empty; thiếu nghĩa là đã có kết quả được công nhận nhưng chưa đủ; thất bại nghĩa là đã xử lý mà chưa có kết quả nào được công nhận cho bài. PDF-only tính vào thiếu. Số đếm gồm cả dữ liệu tốt được dùng lại khi resume, không phải số request hoặc task. Các dòng này dùng cùng quy tắc đầy đủ với quality report và không thay thế thống kê T6.

### Chạy mẫu thật

```bash
PYTHONPATH=src python -m arxiv_pipeline.runner \
  --owner trong --run-id baseline-trong-01 \
  --max-candidates 10 --max-papers 5 --output-root data/m1-trong-01
```

`--max-candidates` là số ID ứng viên T1 kiểm tra; `--max-papers` là số bài đã xác minh tối đa được chọn cho mỗi owner. Hai giới hạn mặc định là 10 và 5; không bảo đảm 10 ứng viên tìm được 5 bài. Có thể lặp `--owner` hoặc dùng `--all-owners`. Chỉ dùng `--full-range` khi chủ động chạy toàn dải; cờ này không đi cùng hai giới hạn mẫu. Lượt mẫu có `discovery_complete=false` cho dải được giao.

### Ngắt và chạy tiếp

```bash
# Có thể dừng có chủ đích ở ranh giới task để thử resume.
PYTHONPATH=src python -m arxiv_pipeline.runner \
  --mock --owner trong --run-id t5-resume --output-root data/t5-resume \
  --stop-after-tasks 3

PYTHONPATH=src python -m arxiv_pipeline.runner \
  --resume --run-id t5-resume --output-root data/t5-resume
```

Resume đọc mode, owner và giới hạn đã lưu; không truyền lại các cờ cấu hình đó. Giữ cùng run_id/output root. Run mới dùng output root mới; runner từ chối nhận thư mục dữ liệu có sẵn không có state tương ứng.

Trước khi bỏ qua task đã công nhận, runner đối chiếu file, kích thước và SHA-256 với SQLite. File mất/hỏng được đưa về diện cần xử lý. Discovery tiếp tục theo checkpoint từng lô; nguồn tải dở vẫn thử lại theo task, chưa tiếp tục theo byte. References tiếp tục theo trang khi runner truyền checkpoint store chung cho `ReferencesModule` (xem "T4 resume"). Nguồn tốt còn hợp lệ được dùng lại. Retry lỗi không thay bộ dữ liệu tốt bằng kết quả lỗi; partial giữ phần đã có và được thử bổ sung ở lần resume tiếp theo.

Downloader kiểm tra HTTP status, Content-Length nếu có và từ chối phản hồi từng phần cho một GET toàn bộ. T3 thử lại tải thiếu hoặc dữ liệu nén kết thúc sớm, với tổng tối đa 3 request trong một lần fetch nguồn bằng client thật; retry tải và giải nén dùng chung ngân sách. GZIP/TAR phải vượt kiểm tra kết thúc stream trước khi bàn giao. Provenance nguồn ghi trạng thái HTTP, số byte kỳ vọng/thực nhận và Content-Range khi có. PDF-only vẫn là unsupported; archive không an toàn hoặc vượt giới hạn không được tải lại vô ích.

Khi Semantic Scholar trả 429, khoảng chờ dự phòng tăng 30 → 60 → 120 giây và giữ 120 giây nếu tiếp tục bị giới hạn; HTTP 200 đặt lại mức chờ. Luôn đợi ít nhất Retry-After hợp lệ của máy chủ, kể cả khi dài hơn mức dự phòng hoặc ở dạng HTTP-date. Khoảng chờ dùng chung áp dụng cả sang bài tiếp theo sau khi hết 3 attempts. Nếu `RequestScheduler` được tạo với `checkpoints=` (checkpoint store của runner), cooldown, khoảng cách tối thiểu giữa các request, chuỗi 429 của Semantic Scholar và cửa sổ 100 request/300 giây được lưu và khôi phục sau khi khởi động lại (xem mục "T4 resume"); không truyền thì vẫn chỉ nằm trong tiến trình. References chưa lấy đủ vẫn được ghi failed/partial để bổ sung bằng resume.

Chạy bổ sung lượt mẫu thật đã có, giữ nguyên cấu hình đã lưu:

```bash
PYTHONPATH=src python -m arxiv_pipeline.runner \
  --resume --run-id baseline-trong-05 --output-root data/baseline-trong-05
```

Sau khi runner dừng, đọc validation.json, quality.json và scope.json trong thư mục runs của lượt chạy. Validation hợp lệ xác nhận cấu trúc và artifacts; quality còn issue cần được đối chiếu theo bài/phiên bản. Không coi PDF-only là đã thu thập TeX thành công hoặc coi một đợt mẫu là đã xử lý toàn dải.

### Thử trên Google Colab CPU

Upload [src/colab_baseline_smoke.ipynb](src/colab_baseline_smoke.ipynb) lên Colab, chọn runtime CPU rồi chạy lần lượt các cell. Notebook tải đúng commit baseline `5db78b0`, chạy tối đa 5 bài trong 10 ID ứng viên của Trọng, hiện tiến độ, đo thời gian runner và tạo gói `.tar.gz` kèm SHA-256 để tải về. Lượt chạy dùng dữ liệu thật; mã thoát 2 có thể do PDF-only hoặc API chưa trả đủ dữ liệu, cần đối chiếu các báo cáo.

Đây là phép thử môi trường, chưa phải benchmark chính thức. Dữ liệu làm việc nằm trong `/content/`; notebook chưa tự sao lưu lên Drive hoặc khôi phục sau khi máy ảo bị xóa. Chạy tiếp trong cùng phiên bằng `run_sample(resume=True)` rồi xuất lại gói kết quả. Muốn so tốc độ với máy cá nhân, dùng cùng commit, mẫu, cấu hình API key và output mới cho mỗi lượt đầu.

### Đọc kết quả

```text
<output-root>/
├── state.sqlite
├── discovery/<owner>.json
├── raw/<owner>/<paper-folder>/
│   ├── metadata.json
│   ├── references.json
│   ├── raw_references.json
│   └── vN/                 # .tex/.bib và source_manifest.json
├── logs/<run-id>/events.jsonl
└── runs/<run-id>/
    ├── scope.json
    ├── accepted_results.jsonl
    ├── validation.json
    └── quality.json
```

`validation.json` đánh giá đúng cấu trúc, identity và artifacts; `quality.json` đánh giá dữ liệu trong mẫu đã đầy đủ chưa. Partial có thể đúng schema nhưng chưa đủ dữ liệu. Sources unsupported giữ record phiên bản, không được tính là đã lấy đủ TeX. References rỗng ở tập arXiv vẫn có thể có dữ liệu trong raw_references.json.

`events.jsonl` giữ lịch sử các lần gọi có Result hợp lệ; `accepted_results.jsonl` chỉ có một kết quả đang được công nhận cho mỗi task. T6 đọc **scope.json + accepted_results.jsonl khi runner đã dừng và validation hợp lệ**, không lấy dòng log cuối làm kết quả chính thức. Nếu resume thì đợi runner xuất lại bộ file; hai file handoff không được cập nhật trong lúc T6 đọc.

Runner đặt `validation.valid=false` trước khi bắt đầu/resume hoặc cập nhật handoff, rồi chỉ ghi `valid=true` sau khi cả scope và accepted results đã được xuất thành công. Khi Ctrl+C giữa một publication, runner phục hồi journal trước khi kiểm tra và xuất lại; một bộ file dở không làm mất liên kết tới kết quả tốt trước đó.

Mã thoát: `0` khi mẫu được xử lý không còn issue chất lượng; `2` khi còn task thiếu hoặc dừng theo giới hạn số task; `1` khi lỗi cấu trúc/cấu hình/lưu trữ; `130` khi Ctrl+C. Argparse dùng mã 2 cho lỗi cú pháp lệnh. Chạy hết chương trình không có nghĩa đã thu thập đủ toàn dải.

## Kiểm thử offline

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Test không gửi request thật và dùng thư mục tạm. T1 gồm dải qua tháng, đầu mút, giới hạn thử, ID trống, phiên bản/trùng thứ tự, Atom lỗi, phân trang thiếu, HTTP retry và CLI. T2 gồm một/nhiều phiên bản, thiếu ngày, thiếu v1, khoảng trống, not_found, bản ghi bị xóa, lỗi mạng/HTTP, payload lặp hoặc hỏng, ID không hợp lệ, tác giả có đơn vị lồng nhau và CLI với các file trong `tests/fixtures/metadata/`.

## Schema và tài liệu

Cả nhóm dùng **schema 1.2** theo tài liệu bên dưới. `SCHEMA_VERSION` trong `src/arxiv_pipeline/contracts.py` là hằng phiên bản dùng chung cho T1–T4 và generator. Phiên bản mới bổ sung `data.raw_references` bắt buộc cho T4. File contracts cũng mô tả kiểu dữ liệu của từng task, kiểm tra và thống kê; TypedDict không tự kiểm tra dữ liệu lúc chạy. Validator runtime nằm trong `src/arxiv_pipeline/validation/`; runner T5 kiểm tra trước khi công nhận dữ liệu.

Tạo dữ liệu mẫu offline bằng thư mục mới hoặc rỗng:

```bash
PYTHONPATH=src python -m arxiv_pipeline.examples --output-dir /tmp/arxiv-m1-examples-v12
```

Generator tạo Result mẫu cho năm thành viên, archive thử, cả thư mục staging và corpus mô phỏng, cùng scope/accepted_results cho phần thống kê. Mẫu references có cả tập arXiv và dữ liệu gốc ngoài arXiv; một mẫu empty vẫn giữ raw không rỗng. Nó mô phỏng đầu ra bàn giao, không phải runner thật. Các mẫu đều `is_mock=true`, request thực tế bằng 0; lỗi 429/404 trong edge_cases là tình huống giả lập. Không đưa mẫu vào báo cáo dữ liệu thật. Mẫu 1.0/1.1 cần tạo lại; không đổi nhãn phiên bản hoặc tự thêm raw_references=[] vì không khôi phục được các mục đã bỏ.

- [Schema 1.2 và phân công](docs/superpowers/specs/2026-10-16-milestone-1-schema-and-tasks.md)
- [Kế hoạch theo tuần](docs/superpowers/plans/2026-10-14-milestone-1-team-plan.md)
- [Đề bài milestone 1 cập nhật](context/crawling_new.md)
- [Hướng dẫn API arXiv](https://info.arxiv.org/help/api/user-manual.html)
- [Điều khoản truy cập API](https://info.arxiv.org/help/api/tou.html)

Mã nguồn không tự commit, push hoặc xuất bản dữ liệu.
