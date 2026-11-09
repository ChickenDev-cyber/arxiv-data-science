# arXiv Data Science — Milestone 1

Repo mã nguồn cho đồ án Nhập môn Khoa học Dữ liệu: thu thập bài báo arXiv, phân tích nội dung và đối sánh tài liệu tham khảo. Đã có T1 discovery, T2 metadata/lịch sử phiên bản, T3 nguồn TeX/BibTeX, T4 references và **T5 runner theo công đoạn, có SQLite và checkpoint discovery theo lô**. Đã tích hợp validator, snapshot/restore, collection, thống kê và benchmark. Xem [hướng dẫn crawl Colab](#chay-chinh-thuc); độ phủ và hiệu năng toàn dải vẫn phải được xác nhận bằng lượt chạy thật.

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

Transport mặc định của Semantic Scholar dùng `http.client.HTTPSConnection` để giữ nguyên tên header `x-api-key` khi gửi. Phép đối chiếu trên Colab với cùng key, URL và tham số đã trả 200 với tên viết thường nhưng 429 với `X-Api-Key`; vì vậy chỉ kiểm tra dictionary headers trước khi gửi là chưa đủ. Preflight và crawl dùng chung transport này. Kết quả đó không bảo đảm mọi request sau này đều tránh được 429: lịch request, retry và cooldown vẫn được giữ.

Transport này kết nối HTTPS trực tiếp đến `api.semanticscholar.org`, không tự theo redirect và không đọc cấu hình proxy của `urllib`. Client vẫn nhận `transport=` tùy chỉnh cho môi trường cần proxy. Các client arXiv giữ nguyên transport cũ; số lần thử HTTP, trường dữ liệu và schema không đổi.

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
| Chạm giới hạn số trang mới (`max_pages`, mặc định 50) khi danh sách chưa hết | partial | false | `error.code=remote_error`, `retryable=true`; resume chỉ tính các trang tải mới trong lượt hiện tại |
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
| `references` | `["<owner>", "<arxiv_id>", <page_size>, "<fields>"]` | `owner`, `arxiv_id`, `page_size`, `fields`, `pages[]`; mỗi trang có `offset`, `next_offset`, `raw_items`, `fetched_at` |
| `arxiv_dates` | `["<cited_arxiv_id>"]` | `arxiv_id`, `submission_date`, `source`, `fetched_at`; `run_id` được cô lập ở tầng store |
| `rate_limits` | `["arxiv"]` hoặc `["semantic_scholar"]` | `group`, `next_allowed_at` (UTC ISO 8601), `recent_requests_at` (danh sách UTC ISO 8601), `consecutive_429` |

- `ReferencesModule(reader, dates, checkpoints=store)` lưu sau mỗi trang được chấp nhận. Key checkpoint trang gồm đủ `owner`, `arxiv_id`, `page_size` và `fields`; checkpoint hoàn tất được dùng lại. `max_pages` chỉ đếm trang tải mới trong lượt chạy hiện tại, vì vậy resume vẫn có thể lấy trang 51 sau khi đã lưu 50 trang. Muốn xóa checkpoint, gọi `PageCheckpoint(store).discard(owner, arxiv_id, page_size, fields)`.
- Cache ngày chỉ lưu ngày hợp lệ lấy từ arXiv API v1 (`published`), kèm nguồn và thời điểm lấy. ID không được API trả về không bị cache thành “không tồn tại”; batch lỗi chỉ khiến các ID chưa có ngày được hỏi lại.
- Store dùng validator envelope chung của runner cùng validator payload T4. Key là chuỗi JSON chuẩn tắc biểu diễn mảng không rỗng. Checkpoint hỏng, sai khóa hoặc sai phiên bản được báo lỗi rõ ràng, không âm thầm tải lại từ đầu. CLI T4 đặt toàn bộ checkpoint dưới thư mục `state-dir/<run_id>` để cô lập trang, ngày và lịch chờ theo run; key `arxiv_dates` vẫn đúng schema chung `["<cited_arxiv_id>"]`. `provenance.pages_resumed` và `dates_from_cache` cho biết phần dùng lại.
- Scheduler phải lưu trạng thái đặt chỗ request trước khi gửi request; nếu ghi thất bại thì request không được gửi. Lịch chờ lưu bằng UTC ISO 8601 vì đồng hồ monotonic không sống qua restart; khi khôi phục deadline `Retry-After` được giữ nguyên, không cắt xuống 24 giờ. Scheduler lưu cooldown, khoảng cách tối thiểu giữa request, chuỗi 429 và cửa sổ 100 request/300 giây của Semantic Scholar.

Chạy thử riêng lẻ (cần mạng): `PYTHONPATH=src python -m arxiv_pipeline.references --owner man-nhi --arxiv-id 2501.12642 --run-id demo --state-dir /tmp/t4-state`; trạng thái của run này nằm dưới `/tmp/t4-state/demo`. Ctrl+C rồi chạy lại cùng lệnh sẽ tiếp tục từ trang đang dở; đổi `--run-id` sẽ dùng một store riêng. Test offline: `tests/test_references_resume.py`.

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

**Trạng thái tích hợp:** đã có checkpoint discovery, adapter lưu trữ, validator document, inspector/collection và snapshot CLI. T4 dùng checkpoint trang references, cache ngày và cooldown qua restart: `LiveModuleFactory` truyền cùng store cho `ReferencesModule` và `RequestScheduler` qua keyword `checkpoints`; constructor vẫn chấp nhận `store` như bí danh tương thích ngược. Payload T4 tuân thủ TypedDict trong `pipeline_contracts.py`, key JSON chuẩn tắc và thời gian ISO 8601. CLI kiểm tra bằng `validate_pipeline_document(..., kind="checkpoint")`; lỗi constructor/validator được báo rõ. HTTP Range chưa được triển khai.

**Validator tài liệu vận hành** (`validation/pipeline_contracts.py`, Huỳnh Nhi): `validate_pipeline_document(document, kind)` với `kind` là `checkpoint`, `snapshot`, `collection` hoặc `collection-statistics`, trả `list[str]` (rỗng là hợp lệ). Kiểm tra cấu trúc theo `pipeline_contracts.py` rồi quy tắc giữa các trường: key checkpoint khớp payload (references là `[owner, arxiv_id, page_size, fields]`), ID của batch discovery đúng dải được giao tại offset đó, trang references liền nhau, hash/đường dẫn của snapshot, member trỏ `<snapshot>/corpus` cạnh `<snapshot>/manifest.json`, mỗi owner một snapshot, `missing_owners` đúng, `discovery_complete` chỉ khi đủ năm dải, số đếm cộng đúng theo owner, số đếm con không vượt số đếm cha (ví dụ references đủ metadata ≤ bài lấy hết trang, số mẫu dung lượng ≤ phiên bản nguồn thành công) và trung bình chung có trọng số theo số mẫu. Key checkpoint được so theo văn bản JSON nên `true` khác `1`. Input sai (kể cả `kind` lạ) được trả về trong danh sách lỗi, không ném exception.

**Kiểm tra run và tập hợp snapshot** (`collection/`, Huỳnh Nhi): `RunInspector().open(output_root, run_id)` giữ khóa `.runner.lock` của output root như runner, mở một run đã dừng ở chế độ chỉ đọc và trả `VerifiedRun` sau khi kiểm tra SQLite, file/hash, events và bộ file bàn giao; `files` là inventory đủ để chép sang nơi khác rồi mở lại. `CollectionAssembler(run_reader)` mở và kiểm tra mọi run bằng `RunReader` rồi mới ghi `collection.json` cho các snapshot đã giải nén, báo lỗi nếu một owner có hơn một snapshot và không ghi đè collection đã có; `CollectionService(snapshot_reader, run_reader)` có `assemble(archives, destination, *, collection_id)` (giải nén qua `SnapshotReader`, kiểm tra rồi mới ghi) và `open(collection_root)` (`CollectionReader`); `CollectionDirectoryReader(RunInspector()).open(root)` kiểm tra lại từng snapshot và run. `CollectionStatisticsBuilder(summarizer)` cộng số đếm theo owner rồi tính lại tỷ lệ chung từ tổng số đếm; thống kê từng owner do T6 cung cấp qua `OwnerSummarizer`. Test inspector cần Linux/macOS (runner dùng `fcntl`).

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

Dùng [src/colab_baseline_smoke.ipynb](src/colab_baseline_smoke.ipynb) và làm theo [hướng dẫn chạy chung](#chay-chinh-thuc). Notebook lấy commit cụ thể do leader cung cấp, hỗ trợ từng owner, đo cùng lúc với crawl và xuất snapshot để bàn giao qua Drive chung. Lượt mẫu vẫn phải ghi rõ phạm vi; chỉ kết quả chạy thật trên Colab CPU mới được mô tả là phép đo trên môi trường đó.

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

## Kiểm tra run và tập hợp snapshot (collection)

`collection/` đọc các lượt chạy đã dừng và các snapshot đã giải nén, **không ghi gì vào corpus hay SQLite**; chỉ lệnh `assemble` ghi đúng một file `collection.json`. Cả ba lệnh `inspect`/`assemble`/`check` đều mở run bằng runner state nên cần Linux/macOS/Colab (runner dùng `fcntl`).

### 1. Kiểm tra một run đã dừng

Input: thư mục output của runner (có `.runner.lock`, `state.sqlite`, `logs/<run-id>/events.jsonl` và `runs/<run-id>/`), run đã `paused` hoặc `finished`. Trong lúc kiểm tra, lệnh giữ khóa output root nên không runner nào chạy song song trên thư mục đó.

```bash
PYTHONPATH=src python -m arxiv_pipeline.runner --mock --owner huynh-nhi --run-id nhi-demo --output-root data/nhi-demo
PYTHONPATH=src python -m arxiv_pipeline.collection inspect --output-root data/nhi-demo --run-id nhi-demo
```

Output (stdout, JSON):

| Trường | Ý nghĩa |
|---|---|
| `run_id`, `run_state`, `is_mock` | Lượt chạy và trạng thái (`paused`/`finished`) |
| `owners`, `papers`, `accepted_results` | Owner của run, số bài trong scope, số Result được công nhận |
| `files` | Số file của inventory đã kiểm tra kích thước và SHA-256: dữ liệu được công nhận, 4 file bàn giao, `logs/<run-id>/events.jsonl`, `state.sqlite`, `.runner.lock` |
| `quality_valid` | `true` khi mọi bài đã đủ metadata, nguồn và references (`false` vẫn đọc được, việc còn thiếu nằm trong `issues`) |
| `issues` | **Danh sách vấn đề**: mỗi mục `{owner, arxiv_id, version, step, message}` lấy từ `quality.json` |

Run có cấu trúc sai (đang chạy, runner khác đang giữ khóa, file bị đổi/mất, `validation.json` không hợp lệ, `scope.json`/`accepted_results.jsonl`/events khác SQLite, `quality.json` sai cấu trúc hoặc thiếu vấn đề SQLite phát hiện) không được đọc: lệnh báo lỗi ở stderr, exit 1.

### 2. Tập hợp snapshot thành collection

Input: một thư mục chứa các snapshot **đã giải nén** theo cấu trúc đã thống nhất: `manifest.json` (SnapshotManifest) nằm cạnh `corpus/`, và `corpus/` là output root của run chứa đúng các file trong `files` của manifest (đường dẫn tính từ `corpus/`):

```text
nhom5/
└── snapshots/
    ├── nhi-1027/
    │   ├── manifest.json
    │   └── corpus/      .runner.lock, state.sqlite, logs/, raw/, runs/, ...
    └── trong-1027/
```

```bash
PYTHONPATH=src python -m arxiv_pipeline.collection assemble --root nhom5 --collection-id nhom5-demo \
  snapshots/nhi-1027 snapshots/trong-1027
```

Output: mỗi owner chỉ được có **một** snapshot; nếu hai snapshot cùng chứa một owner thì lệnh báo lỗi, không tự chọn bản nào. Snapshot được giữ nguyên cả cụm, không tách owner.

```json
{
  "collection_id": "nhom5-demo",
  "is_mock": true,
  "members": [
    {"snapshot_id": "nhi-1027", "owners": ["huynh-nhi"]},
    {"snapshot_id": "trong-1027", "owners": ["trong"]}
  ],
  "manifest": "nhom5/collection.json"
}
```

`nhom5/collection.json` được ghi nguyên tử và chỉ ghi một lần; nếu file đã có, lệnh báo lỗi thay vì ghi đè:

```json
{
  "format_version": 1,
  "kind": "arxiv-collection",
  "collection_id": "nhom5-demo",
  "schema_version": "1.2",
  "is_mock": true,
  "created_at": "2026-10-27T15:21:44Z",
  "members": [
    {"snapshot_id": "nhi-1027", "run_id": "nhi-1027-run", "owners": ["huynh-nhi"],
     "exporter_commit": "<40 ký tự hex>", "root": "snapshots/nhi-1027/corpus",
     "manifest_path": "snapshots/nhi-1027/manifest.json"},
    {"snapshot_id": "trong-1027", "run_id": "trong-1027-run", "owners": ["trong"],
     "exporter_commit": "<40 ký tự hex>", "root": "snapshots/trong-1027/corpus",
     "manifest_path": "snapshots/trong-1027/manifest.json"}
  ]
}
```

Trước khi ghi, lệnh mở từng `corpus/` như `inspect` và đối chiếu manifest với run (như `check` ở mục 3); các run được giữ khóa cho đến khi ghi xong.

Bị từ chối (exit 1): `collection.json` đã có, hai snapshot cùng owner, manifest sai schema, thiếu `corpus/` hoặc file trong `corpus/` sai kích thước/hash, manifest khác run (ví dụ snapshot thiếu `state.sqlite`/`.runner.lock` mà run có), cùng `snapshot_id` hai lần, trộn mock với dữ liệu thật, đường dẫn ra ngoài `--root`.

### 3. Kiểm tra lại toàn bộ collection

```bash
PYTHONPATH=src python -m arxiv_pipeline.collection check --root nhom5
```

Lệnh đọc `collection.json`, kiểm tra lại hash từng snapshot, mở từng `corpus/` như `inspect`, rồi đối chiếu manifest với run: `run_id`, owner, fingerprint, `origin_output_root` bằng `recorded_output_root` của run, `producer_commits`/`unknown_provenance_tasks` khớp `provenance.producer_commit` của các Result được công nhận và `files` bằng đúng inventory của run. Sau đó in:

| Trường | Ý nghĩa |
|---|---|
| `collection_id`, `is_mock` | Collection được kiểm tra |
| `members` | Mỗi snapshot: `snapshot_id` cùng các trường của `inspect` (trừ `issues`) |
| `missing_owners` | Owner chưa có snapshot nào, theo thứ tự `ASSIGNED_RANGES` |
| `issues` | Danh sách vấn đề của mọi run, thêm `snapshot_id` |

### Exit code

| Exit code | Kết quả |
|---|---|
| 0 | Hợp lệ và đầy đủ (`inspect`: không còn issue; `check`: không issue, đủ 5 owner; `assemble`: đã ghi collection.json) |
| 2 | Hợp lệ nhưng chưa đầy đủ: còn issue chất lượng hoặc còn owner thiếu |
| 1 | Run/snapshot/collection sai cấu trúc, không đọc được, hoặc chạy trên Windows |

### Dùng từ Python

```python
from pathlib import Path
from arxiv_pipeline.collection import CollectionService, CollectionStatisticsBuilder
from arxiv_pipeline.collection.inspector import RunInspector
from arxiv_pipeline.snapshots import SnapshotService

service = CollectionService(SnapshotService(), RunInspector())
manifest = service.assemble([Path("exports/nhi-1027.tar.gz"), Path("exports/trong-1027.tar.gz")],
                            Path("nhom5"), collection_id="nhom5-demo")

with service.open(Path("nhom5")) as collection:
    stats = CollectionStatisticsBuilder(summarizer).build(collection)
```

`assemble` nhận thư mục đích mới hoặc rỗng, giải nén từng archive vào `snapshots/<snapshot_id>/` (manifest cạnh `corpus/`), báo lỗi khi trùng snapshot hoặc trùng owner, mở và kiểm tra mọi run, rồi mới ghi `collection.json`; lỗi ở bất kỳ bước nào thì không có `collection.json`. `open` là `CollectionReader.open`, kiểm tra lại mọi snapshot và run. `create(archives, collection_root, collection_id)` vẫn giữ để tương thích và gọi `assemble`.

`summarizer` là đối tượng có `summarize(run, owner) -> Summary` (thống kê từng owner của T6). `build` cộng số đếm theo owner và tính lại tỷ lệ theo công thức mục 11 tài liệu schema; `by_owner` chỉ gồm owner có bài trong `paper_tasks`. `scope.versions_complete` được tính lại từ metadata đã công nhận của mọi bài trong scope hợp nhất, không lấy cờ của từng run.

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

## Đóng gói và khôi phục corpus

`arxiv_pipeline.snapshots` cung cấp `export`, `verify`, `unpack` và `restore`.
Archive `.tar.gz` chứa `manifest.json` cạnh `corpus/`; manifest ghi inventory,
SHA-256, phạm vi, nguồn code đã biết và số Result chưa biết nguồn code.
Snapshot giữ nguyên schema dữ liệu 1.2 và không biến lượt mẫu thành toàn dải.

CLI dùng validator document chung và `RunInspector` của module collection;
các dependency này đã được tích hợp và kiểm tra qua snapshot/restore thật. Lớp `SnapshotService` nhận `RunReader`, `StateRelocator`
và validator qua constructor để kiểm thử độc lập, không viết lại phần của
thành viên khác.

Xuất một corpus đã dừng vào đường dẫn mới
**bên ngoài output root**:

```bash
PYTHONPATH=src python -m arxiv_pipeline.snapshots export \
  --output-root data/m1-trong --run-id m1-trong \
  --destination exports/trong-001.tar.gz --snapshot-id trong-001 \
  --exporter-commit "$(git rev-parse HEAD)"

PYTHONPATH=src python -m arxiv_pipeline.snapshots verify \
  --archive exports/trong-001.tar.gz

PYTHONPATH=src python -m arxiv_pipeline.snapshots unpack \
  --archive exports/trong-001.tar.gz --destination data/trong-readonly
```

`exporter-commit` là SHA của code đóng gói đã commit; chỉ dùng `git rev-parse HEAD`
khi thay đổi code liên quan đã được commit. Nó không thay thế provenance của
những Result đã thu thập trước đó. Không tự gán revision mới cho dữ liệu cũ.

`unpack` bảo toàn bản bàn giao để đọc/tổng hợp. Muốn tiếp tục crawl tại máy mới,
dùng `restore` với một đích khác chưa tồn tại:

```bash
PYTHONPATH=src python -m arxiv_pipeline.snapshots restore \
  --archive exports/trong-001.tar.gz --destination data/trong-restored

PYTHONPATH=src python -m arxiv_pipeline.runner --resume \
  --run-id m1-trong --output-root data/trong-restored/corpus
```

Restore chỉ cập nhật output root của DB bản sao qua `StateRelocator`, kiểm tra
lại corpus rồi ghi `restore_receipt.json` cạnh `manifest.json`. Manifest gốc
mô tả DB trước relocate; không sửa hash trong manifest để che thay đổi này.
Nếu relocate thất bại sau khi unpack, bản đã giải nén được giữ để kiểm tra,
không có receipt xác nhận restore thành công. Archive và corpus gốc không bị đổi.

Không ghi đè archive/đích đã tồn tại. Mặc định giới hạn 100.000 file archive
(gồm manifest), 2 GiB/file và 20 GiB tổng byte giải nén; có thể cấu hình bằng
`--max-files`, `--max-file-bytes`, `--max-total-bytes`. Các số này là giới hạn
kỹ thuật, không phải hạn mức của đề bài. Link, đường dẫn nguy hiểm, file thiếu/thừa,
byte/hash sai và archive hỏng đều bị từ chối trước khi công bố đích unpack.
Nếu filesystem đích không hỗ trợ công bố archive mà không ghi đè, hãy xuất ra
ổ cục bộ trước rồi sao chép gói đã hoàn tất sang nơi lưu trữ.

Mã nguồn không tự commit, push hoặc xuất bản dữ liệu.

<a id="chay-chinh-thuc"></a>
## Crawl chính thức trên Colab CPU

Mỗi thành viên chạy **toàn bộ pipeline cho owner của mình**, vừa crawl vừa đo benchmark trong cùng lượt, dùng [notebook hiện tại](src/colab_baseline_smoke.ipynb). Leader commit/push runner mới rồi điền SHA 40 ký tự đó vào `COMMIT_SHA` trước khi gửi notebook. Bản `af99d119a3dcc0f8f25bfa2c9d5a6b6d9d7bb218` chưa có cơ chế hoãn lỗi theo lượt; notebook mới để trống SHA và kiểm tra capability trước khi crawl để tránh chạy nhầm hành vi cũ. Dữ liệu local đã có giữ làm dự phòng; không ghép trùng với corpus Colab.

| Thành viên | OWNER |
|---|---|
| Trọng | `trong` |
| Khang | `khang` |
| Hoàng | `hoang` |
| Mẫn Nhi | `man-nhi` |
| Huỳnh Nhi | `huynh-nhi` |

**Lịch chạy của nhóm:** bộ điều phối request hiện chỉ dùng chung trong một runner, chưa phối hợp giữa các VM. Trước khi Run all, leader phân ca để chỉ một runner của nhóm gửi request arXiv tại một thời điểm, kể cả Colab, Kaggle và máy local. Theo [điều khoản API arXiv](https://info.arxiv.org/help/api/tou.html), giới hạn một request mỗi ba giây và một kết nối áp dụng cho các máy cộng lại; mỗi máy tự chờ ba giây chưa đủ để điều phối cả nhóm.

**Bản gửi thành viên:** dùng notebook trong repo với cấu hình khởi đầu bên dưới. Không sao chép các cell phục hồi riêng của Trọng (`arxiv-recovery-01`, `RECOVERY_MEASUREMENT_ID`) sang phiên mới của thành viên khác. Log chạy thật `m1-colab-trong-01/resume-02` đã ghi nhận 500 task mới, verify snapshot, đối chiếu SHA-256 trên Drive rồi tự bắt đầu `resume-03`. Đây là bằng chứng chu kỳ sao lưu và tiếp tục; toàn dải và độ đầy đủ dữ liệu vẫn cần đánh giá khi crawl kết thúc.

### 1. Chuẩn bị một lần

Drive chung đã được tạo: [mở thư mục của nhóm](https://drive.google.com/drive/folders/1w9cVHms6Q9aakHKMSJY5DGzmBRAPleeT?usp=drive_link). Notebook lưu link trong `TEAM_DRIVE_FOLDER_URL`, tìm đường dẫn lối tắt theo ID nếu có; `/content/drive/MyDrive/arXiv-data` là đường dẫn dự phòng, cần khớp thư mục thực tế sau mount. Leader chia sẻ quyền chỉnh sửa cho các thành viên; mỗi người thêm shortcut vào My Drive nếu cần và xác nhận đường dẫn trên tài khoản của mình. Đây phải là cùng thư mục đã chia sẻ, không phải các thư mục riêng cùng tên. [Hướng dẫn shortcut](https://support.google.com/drive/answer/9700156).

Mở notebook trên Colab, chọn **Hardware accelerator: None (CPU)**. Điền commit mới vào cell cấu hình, kiểm tra Drive và đổi `OWNER` theo bảng; `RUN_ID` được tạo theo owner. Cấu hình khởi đầu:

```python
COMMIT_SHA = "<SHA 40 ký tự của commit runner mới do leader cung cấp>"
PREVIOUS_PIPELINE_COMMIT = ""  # chỉ dùng khi nâng code từ snapshot cũ
OWNER = "trong"
RUN_ID = f"m1-colab-{OWNER}-01"
RUN_MODE = "full"
MEASUREMENT_ID = "cold-01"
STOP_AFTER_TASKS = 500
STOP_AFTER_SECONDS = 600
S2_DELAY_SECONDS = 1.0  # giữ nhịp cũ; có thể thử 2.0 trong một batch nhỏ
REFERENCES_RATE_LIMIT_THRESHOLD = 2
REQUIRE_S2_KEY = True
AUTO_BATCHES_PER_CALL = None  # đặt 1 để thử một đợt, giữ nguyên ngân sách tổng
AUTO_RETRY_PASSES = 2
AUTO_MAX_BATCHES = 200
SNAPSHOT_MAX_FILES = 500_000
TEAM_DRIVE_FOLDER_URL = "https://drive.google.com/drive/folders/1w9cVHms6Q9aakHKMSJY5DGzmBRAPleeT?usp=drive_link"
TEAM_DRIVE_DIR = "/content/drive/MyDrive/arXiv-data"
CORPUS_PATH = ""
RESTORE_BUNDLE = ""
```

`full` chọn toàn dải, không dùng hai giới hạn mẫu `MAX_CANDIDATES`/`MAX_PAPERS`. `STOP_AFTER_TASKS` là số task mới trong một invocation, không phải số bài; discovery cả dải là một task nên có thể lâu. Phần cứng được đọc thực tế; không mặc định mọi phiên Colab có cùng CPU/RAM.

### 2. Run all để crawl, đo và sao lưu tự động

Sau khi đổi `OWNER` và xác nhận đường dẫn Drive, chọn **Runtime → Run all** rồi cấp quyền mount Drive. Notebook lần lượt chạy:

```text
Preflight với key → tối đa 500 task hoặc mốc mềm 600 giây → kiểm tra corpus → xuất thống kê → snapshot + verify
→ chép ZIP/checksum và số liệu lên Drive → đối chiếu SHA-256 → resume
```

Không cần tự đổi measurement ID sau mỗi đợt. Notebook cấp `cold-01`, `resume-01`, `resume-02`… và in tổng kết mỗi đợt. `STOP_AFTER_TASKS=500` chỉ dừng invocation hiện tại để sao lưu; vòng điều phối tiếp tục cho đến điều kiện dừng. Discovery cả dải là một task nên đợt đầu có thể lâu.

Thêm `S2_API_KEY` trong **Colab Secrets** (biểu tượng chìa khóa), bật quyền truy cập cho notebook rồi chạy ô nạp key. Không đưa key vào code, log hay Drive. Trước mỗi batch mới, notebook gửi một request references với `limit=1`, dùng cùng key với runner. Probe nằm ngoài benchmark; khi resume, request này dùng checkpoint scheduler chung. Nếu thiếu key, còn cooldown hoặc probe lỗi thì chưa tạo pending batch mới. Khôi phục một backup đang chờ không cần key. Core CLI vẫn cho phép không key; notebook chạy thật mặc định yêu cầu key.

`S2_DELAY_SECONDS` truyền thành `--s2-delay-seconds` trong runner: số giây nghỉ sau khi một HTTP attempt Semantic Scholar kết thúc, hữu hạn và ít nhất 1. Mặc định vẫn là 1 giây. Thử 2 giây có thể giảm lỗi 429 nhưng cần số đo Colab để kết luận; giới hạn cửa sổ 100 request/300 giây, cooldown, `Retry-After` và nhịp arXiv vẫn giữ nguyên. Worker chờ ít nhất khoảng này sau preflight trước khi chạy runner. Lệnh chứa cấu hình được giữ trong `invocation.json` ở VM và `run.json` trong gói sao lưu. Mọi chờ/retry trong runner vẫn tính vào wall time; preflight và thời gian chờ ngay sau probe nằm ngoài phép đo runner. Khi so sánh các đợt, giữ cả log lỗi, phạm vi task mới và commit tương ứng; mẫu 20 task là kiểm tra vận hành, chưa đủ để kết luận nhanh hơn hoặc thay benchmark cuối.

`STOP_AFTER_SECONDS=600` kiểm tra giữa các task sau bước phục hồi, cho task hiện tại hoàn tất. Thời gian phục hồi, task dài, kiểm tra và sao lưu có thể làm một batch vượt 10 phút. Runner ghi lý do dừng trong `runs/<run_id>/execution.json`; notebook đối chiếu với trạng thái và attempt mới nhất trước khi dùng.

Hai Result references mới liên tiếp có HTTP 429 ở bước lấy trang **Semantic Scholar** sẽ dừng runner. Lỗi 429 ở bước lấy ngày nộp bài từ **arXiv**, khi danh sách S2 đã đầy đủ (`data.complete=true`), giữ trạng thái `partial` và các trường còn thiếu, tiếp tục bài kế tiếp theo mốc task/thời gian; lỗi này không kích hoạt vòng phục hồi S2. Result mới ghi dịch vụ gây lỗi trong `provenance.error_source`; Result cũ vẫn được đọc bằng trạng thái đầy đủ của danh sách. Khi đến lượt retry, checkpoint trang S2 và cache ngày đã lấy thành công tiếp tục được dùng.

Khi S2 thật sự kích hoạt guard, notebook kiểm tra, sao lưu và xác minh Drive rồi trả `stop_reason=rate_limited`; không tiêu thụ thêm lượt retry/stall vì lý do này. Chế độ mặc định yêu cầu bạn chủ động chạy lại; nếu đã bật `AUTO_RECOVER_RATE_LIMITS=True`, vòng tự phục hồi chờ và kiểm tra lại trong ngân sách cấu hình. Giữ nguyên retry HTTP, cooldown và `Retry-After` đã lưu. Preflight phải thành công mới tạo batch tiếp theo. Việc sửa nguồn lỗi không làm tăng hạn mức API và chưa phải bằng chứng tăng tốc trên Colab.

Để thử một đợt nhỏ, dùng `STOP_AFTER_TASKS=20`, `AUTO_BATCHES_PER_CALL=1`; đợt thử vẫn giữ nguyên owner/run ID/scope trong snapshot. `stop_reason=session_limit` là đã sao lưu rồi dừng theo giới hạn của lần bấm chạy này. Sau khi kiểm tra backup, đổi lại `500` và `None` để tiếp tục. Không giảm `AUTO_MAX_BATCHES` xuống 1 vì đây là ngân sách cộng dồn qua các VM.

Mốc sao lưu **không mở một lượt retry mới**. Lượt đầu xử lý các task chưa có kết quả hoàn tất; task lỗi được hoãn để tiếp tục metadata/nguồn/references còn mới. Khi đã đi hết dải mới bắt đầu lượt retry, chỉ thử các lỗi được đánh dấu `retryable=true`. Mỗi task tối đa một lần thực thi trong mỗi lượt duyệt, kể cả khi lượt đó gồm nhiều đợt sao lưu. Các lần thử HTTP bên trong một task vẫn theo chính sách mạng hiện có.

Runner nhận `--retry-before-attempt 0` cho lượt đầu; lượt retry dùng ID attempt lớn nhất tại lúc lượt trước kết thúc. Mốc này được giữ nguyên trong journal và continuation của snapshot khi dừng/restore. Log `task hoãn` không có request mới và không tính vào 500 task mới. Task bị ngắt trước khi có Result hợp lệ hoặc artifact đã lưu bị thiếu/hỏng vẫn được xử lý lại để phục hồi. CLI không truyền cờ này giữ hành vi resume cũ.

Sau khi đi hết dải, nếu còn lỗi được module đánh dấu retryable, chương trình thực hiện tối đa **2 lượt thử bổ sung**. Một lượt retry cũng có thể gồm nhiều đợt 500 task. PDF-only hoặc withdrawn không làm lặp vô hạn. Lỗi cấu trúc, lỗi sao lưu, nhiều đợt không tiến triển hoặc trần `AUTO_MAX_BATCHES=200` làm dừng và cần đọc thông báo; dừng vì các lý do này không có nghĩa đã crawl đủ.

Drive có cấu trúc:

```text
arXiv-data/<owner>/<run_id>/<measurement_id>/
  <run_id>-<measurement_id>.zip          # chỉ giữ 2 snapshot mới nhất của run
  <run_id>-<measurement_id>.zip.sha256
  run.json                              # giữ mọi measurement
  benchmark/<run_id>.json
  benchmark/<run_id>-timing.json
  statistics/<run_id>.json
```

ZIP chứa snapshot corpus và các JSON trên. Sau khi kiểm chứng hai bản mới nhất, notebook xóa ZIP/checksum cũ hơn của **đúng owner/run này**; mọi benchmark, timing, statistics và run.json được giữ riêng trên Drive. Bản archive tạm cũ trong VM cũng được dọn, corpus và JSON local được giữ. Drive cần đủ chỗ cho bản mới trước khi dọn bản cũ. Chỉ chạy **một phiên cho mỗi owner/run ID**.

`finished` nghĩa là hoàn tất theo scope; `finished_with_issues` giữ lại phần thiếu, gồm PDF-only/withdrawn hoặc scope pilot có giới hạn; `retry_exhausted` nghĩa là đã dùng hết hai lượt retry. Kiểm tra `quality.json` và `statistics/<run_id>.json` để xem dữ liệu còn thiếu. `validation.valid=true` chỉ xác nhận cấu trúc/artifact nhất quán. Chương trình không đổi lỗi tải thành success.

### 3. Dừng và chạy tiếp trong cùng VM

Khi dùng Stop/Interrupt, notebook cố dừng runner, hoàn tất số đo rồi kiểm tra/sao lưu đợt đang dở. **Đợi thấy xác nhận sao lưu trước khi đóng VM**. Ngắt lần nữa hoặc VM bị thu hồi có thể khiến đợt cuối chưa được sao lưu; chưa có backup nền trong khi runner còn chạy.

Sau lỗi Drive, sửa kết nối/quyền/dung lượng rồi chạy lại cell tự động hoặc Run all với **cùng OWNER, RUN_ID, WORK_ROOT và commit**. Journal local giữ batch đang chờ; notebook hoàn tất backup trước khi chạy tiếp và không crawl lại batch đã đo. Measurement ID và corpus đã restore được nhớ trong VM, không cần sửa cấu hình sau từng đợt. Phiên đã dừng terminal sẽ chỉ trả tổng kết, không tự mở thêm lượt retry.

Nếu chuyển từ notebook cũ, hoàn tất kiểm tra/sao lưu đợt manual cuối trước khi bật auto. Không thay notebook hay khởi chạy thêm runner trong lúc phiên cũ còn crawl.

Nếu sau khi ngắt gặp `Corpus chưa dừng`, không chạy lặp lại hoặc tự sửa trạng thái SQLite. Cần kiểm tra tiến trình và khóa corpus; trạng thái SQLite `running` có thể còn sót sau khi tiến trình đã mất. Giữ nguyên VM và dữ liệu để kiểm tra hoặc khôi phục snapshot đã verify vào thư mục mới.

### 4. Khôi phục khi mất VM

Run all không tự mở lại VM hoặc cấp quyền Google. Trong phiên mới, giữ `OWNER`, `RUN_ID`, commit và Drive; đặt `RUN_MODE="resume"`, điền `RESTORE_BUNDLE` là đường dẫn ZIP **mới nhất** còn trên Drive của run đang tiếp tục. Không chọn snapshot pilot để tiếp tục run toàn dải. Giữ `CORPUS_PATH=""`, rồi Run all và mount Drive.

Cell restore kiểm tra checksum/snapshot và khôi phục vào ổ VM. Gói auto giữ cả số lượt retry đã dùng và measurement kế tiếp nên không đặt lại ngân sách retry. Trong cùng VM, con trỏ local tiếp tục chọn corpus hiện tại, không nạp lại ZIP cũ khi Run all. Nếu chưa có snapshot nào được lưu thành công, không có bản để khôi phục.

**Nâng từ bản runner cũ:** chờ phiên cũ hoàn tất sao lưu và dừng trước khi chuyển bản. Giữ owner/run ID, dùng `WORK_ROOT` mới và restore ZIP mới nhất đã verify; đặt `COMMIT_SHA` thành SHA runner mới, `PREVIOUS_PIPELINE_COMMIT` thành SHA đã tạo gói cũ. Chỉ chấp nhận đúng revision cũ được khai báo, giữ nguyên task/provenance và JSON benchmark cũ, ghi receipt `pipeline-upgrade.json`, chuyển continuation sang code mới. Nếu snapshot đang ở lượt retry cũ mà không có mốc duyệt, notebook dừng để kiểm tra thay vì tự đoán mốc hoặc đặt lại số retry. Không chạy hai phiên cho cùng owner/run và không thay code trong phiên đang crawl.

### 5. Leader nhận và tổng hợp

Giữ JSON của mọi measurement để có đủ lịch sử đo; chọn snapshot mới nhất đã kiểm tra của mỗi owner khi ghép. ZIP cũ hơn hai bản gần nhất đã được dọn theo cấu hình chung. Kiểm tra checksum ZIP, giải nén vào local, dùng snapshot CLI với giới hạn file đã ghi trong run.json. Notebook mặc định 500.000 file; ví dụ:

```bash
PYTHONPATH=src python -m arxiv_pipeline.snapshots verify \
  --archive handoff/snapshot.tar.gz --max-files 500000

PYTHONPATH=src python -m arxiv_pipeline.snapshots unpack \
  --archive handoff/snapshot.tar.gz \
  --destination team/snapshots/trong --max-files 500000
```

Dùng `collection assemble`/`collection check` như mục collection phía trên; mỗi owner một snapshot, xử lý rõ thiếu/trùng. Xuất `statistics --collection <thư mục team> --output-dir <thư mục thống kê>`, chuyển JSON cần thiết sang repo `arvix-report`. Không đưa corpus/SQLite/archive lên GitHub, không cộng thủ công tỷ lệ năm người.

CLI snapshot mặc định vẫn là 100.000 file, 2 GiB/file và 20 GiB tổng byte giải nén. Notebook truyền `SNAPSHOT_MAX_FILES` nhất quán cho export/verify/restore; giới hạn byte và kiểm tra đường dẫn/hash giữ nguyên. 500.000 là trần cấu hình, chưa phải bằng chứng một archive cỡ đó đã chạy thành công.

### 6. Cách trình bày benchmark

Thời gian điều phối, kiểm tra, đóng gói và sao lưu nằm ngoài phép đo benchmark của runner. Giữ riêng cold/mixed và từng invocation, kèm phạm vi và bằng chứng Colab CPU. Mẫu 10–20 bài chỉ là pilot, chưa mặc định đủ phạm vi benchmark cuối theo đề. Chạy theo đợt phải báo cáo từng đợt và giới hạn đo; không gọi một invocation dừng sớm là wall time toàn dải. Không cộng thời gian năm máy thành wall time của một lượt chạy toàn nhóm.

Thời gian từng bài là khoảng elapsed từ task đầu đến cuối trong invocation, có cả chờ giữa công đoạn. Tổng các khoảng từng bài có thể vượt wall time. Resume chỉ đo task mới; discovery không chạy lại là null, không là 0 hay số của lượt trước. [Yêu cầu đề bài](context/crawling_new.md) và [Colab FAQ](https://research.google.com/colaboratory/faq.html).

### Local khi cần

Code Python vẫn hỗ trợ Linux/WSL2 và Python >=3.10; cài `python -m pip install -r src/requirements.txt`. Ví dụ crawl local kèm đo bằng thư mục mới:

```bash
CRAWL_OWNER=trong
CRAWL_RUN_ID="m1-local-$CRAWL_OWNER-01"
CRAWL_OUTPUT_ROOT="data/$CRAWL_RUN_ID"

PYTHONPATH=src python -m arxiv_pipeline.benchmark \
  --run-id "$CRAWL_RUN_ID" --output-root "$CRAWL_OUTPUT_ROOT" \
  --output-dir "benchmarks/$CRAWL_RUN_ID/cold-01" --cache-mode cold \
  --cmd "python -m arxiv_pipeline.runner --owner $CRAWL_OWNER --run-id $CRAWL_RUN_ID --output-root $CRAWL_OUTPUT_ROOT --full-range"
```

Khi dừng, giữ dữ liệu; chạy tiếp qua benchmark với measurement mới, `--cache-mode mixed` và runner `--resume` cùng run ID/output root. Không truyền lại owner hay giới hạn cho runner resume. Số đo local là đối chiếu, không gán thành số đo Colab. Nếu đã chuyển cùng owner sang Colab, giữ corpus local dự phòng thay vì ghép trùng.

### Nguồn thiếu và ngoại lệ

Nếu tải bị cắt, giữ log và để task còn thiếu để chạy tiếp; không bỏ kiểm tra Content-Length hay tăng retry vô hạn. Pilot `2504.17525v1` từng bị cắt ở cả Python/curl nhưng các lượt sau tải đủ; chưa có bằng chứng cần sửa transport hay thêm HTTP Range.

`2504.17532v5` đã xác minh withdrawn trên arXiv, `/src/` của cả hai host trả 404. Giữ `not_found` và metadata đủ năm phiên bản; không đổi thành PDF-only/success hay bỏ khỏi mẫu số. Kèm bằng chứng ngoài corpus, không lặp resume chỉ để thử phiên bản đã xác nhận withdrawn. Không suy rộng rằng mọi 404 đều là withdrawn. References từng bị HTTP429 có thể thử lại bằng resume; giữ riêng số đo mỗi lượt.

### Khôi phục backup bị ngắt khi đã chạy xong batch

Nếu runner đã xuất benchmark/snapshot nhưng notebook lỗi ở bước dọn ZIP sau nhiều lần nâng code, giữ runtime và corpus hiện tại. Với batch `resume-31` đang pending, mở [ô phục hồi backup](src/colab_pending_backup_recovery.py), chép toàn bộ vào một ô mới trong chính notebook đang lỗi và chạy riêng ô đó. Script xác nhận journal/worker/measurement, chỉ hoàn tất backup đã thực thi, tạm giữ tất cả ZIP và chặn cả preflight lẫn task mới; mã producer, số đo, retry boundary và ngân sách được giữ nguyên. Các kiểm tra định danh batch hiện tại, revision corpus và SHA-256 khi sao lưu vẫn chạy. Nếu pending khác hoặc runtime đã mất, script dừng để kiểm tra thay vì tự crawl lại.

Bản notebook hiện tại phân biệt commit của phiên đang chạy với producer của số đo lịch sử khi dọn archive: bản lịch sử vẫn phải đúng owner/run ID/mock/measurement, SHA hợp lệ và checksum ZIP, nhưng có thể thuộc nhiều thế hệ commit. Hai ZIP mới nhất được giữ sau xác minh; JSON của mọi lượt đo được giữ nguyên. Restore và batch đang chạy tiếp tục kiểm tra commit theo cấu hình đã ghim.

### Tự chờ và chạy tiếp khi Semantic Scholar trả 429

Notebook hỗ trợ chế độ tùy chọn:

```python
AUTO_RECOVER_RATE_LIMITS = True
AUTO_RATE_LIMIT_MAX_RETRIES = 48
AUTO_RATE_LIMIT_MAX_WAIT_SECONDS = 21600
AUTO_BATCHES_PER_CALL = None
```

Mặc định notebook chung vẫn tắt chế độ này. Khi bật, lớp điều phối giữ nguyên controller/journal dữ liệu: preflight429/cooldown được chờ rồi kiểm tra lại; batch dừng do429 phải verify/backup xong mới chờ và tiếp tục. Nhịp chờ tăng60/120/240/480/900giây, dùng cooldown hoặc Retry-After đã lưu nếu dài hơn. Nếu cooldown dài hơn ngân sách chờ còn lại thì dừng, không thử sớm để vượt hạn mức. Lỗi key/quyền, cấu trúc dữ liệu, backup hoặc lỗi chưa phân loại vẫn dừng.

`21600` là tối đa6giờ **chờ429**, không phải6giờ crawl. `48` là số lượt phục hồi tối đa, khác với3attemptsHTTP trong mỗi task và2lượt retry dữ liệu sau khi duyệt hết phạm vi. Hai giới hạn mới tính cho mỗi lần bấm auto-run; số batch/retry dữ liệu và cooldown lưu trong corpus vẫn giữ qua các lần chạy và restore. `AUTO_BATCHES_PER_CALL=1` vẫn giới hạn tổng một batch trong lần bấm chạy đó, kể cả có phục hồi;200batch/2lượt dữ liệu cộng dồn không đổi. Colab thu hồi runtime hoặc hết ngân sách vẫn có thể làm dừng; chế độ này không giữ VM sống.

Log điều phối là các JSON nhỏ, bất biến, được chép/đối chiếu lên `<owner>/<run_id>/control-events/<control_id>/` sau mỗi sự kiện. Log có UTC, cấu hình phục hồi, chờ dự kiến/thực tế và elapsed của auto-run; không chứa key/header/body lỗi. Thiếu log Drive làm dừng trước khi gửi thêm request. Có heartbeat trong lúc chờ và khóa local ngăn hai lớp điều phối cùng chạy. Bấm dừng khi đang chờ sẽ trả `interrupted` mà không crawl thêm.

Benchmark runner và sampler giữ nguyên. Wait/retry trong runner vẫn nằm trong wall runner; chờ bổ sung ở preflight/giữa batch được ghi riêng trong control events. Wall của auto-run bao gồm preflight, runner, kiểm tra/đóng gói/Drive và chờ tự phục hồi, nhưng không gồm checkout/restore/Secrets trước ô auto-run; cũng không bổ sung phép đo RAM/đĩa ngoài runner. Khi báo cáo cần giữ rõ phạm vi này, không sửa JSON benchmark cũ hay trừ thời gian chờ.

---

## Milestone 2 — Module 4: Trích xuất BibTeX và chuẩn hóa citation (Mẫn Nhi)

Phần này bổ sung tài liệu cho Module 4 của Milestone 2. **Các nội dung Milestone 1 ở phía trên được giữ nguyên**; Module 4 mới được đặt trong package riêng `bibtex/` và không thay thế module `references/` hiện có.

### Phạm vi và luồng dữ liệu

Module 4 nhận danh sách `GatheredSource` của nhiều phiên bản (`v1`, `v2`, ...) thuộc cùng một bài arXiv, đọc bibliography từ `merged_tex` và các file `.bbl`, rồi trả về:

- `unified_bibtex`: danh sách `BibtexEntry` đã hợp nhất và loại trùng lặp xuyên phiên bản.
- `citation_remap`: ánh xạ citation key theo từng phiên bản, theo dạng `{version: {old_key: canonical_key}}`; Module 2 dùng ánh xạ này để viết lại `\\cite{...}`, còn Module 5 nhận bibliography đã thống nhất.

```text
Module 1 (GatheredSource)
          │
          ▼
Module 4 — arxiv_pipeline.bibtex
  ├── Đọc thebibliography trong merged_tex / .bbl
  ├── Phân tích từng \\bibitem thành BibtexEntry
  ├── Chuẩn hóa DOI và title
  ├── Gộp mục trùng giữa các phiên bản
  └── Tạo citation_remap theo version
       │                         │
       ▼                         ▼
Module 5: unified_bibtex    Module 2: citation_remap
```

### Vị trí mã nguồn

```text
src/arxiv_pipeline/bibtex/
├── __init__.py       # Public API của Module 4
├── contracts.py      # GatheredSource, BibtexEntry, PaperReferences
├── parser.py         # Đọc thebibliography, .bbl và phân tích \bibitem
├── normalizer.py     # Chuẩn hóa DOI và title
├── deduplicator.py   # Dedup xuyên version, hợp nhất trường và tạo remap
├── remapper.py       # Viết lại citation key trong lệnh \cite
└── service.py        # Hàm điều phối extract_and_unify_references

tests/bibtex/
└── test_module4.py   # Kiểm thử parser, dedup, field union và citation remap
```

`src/arxiv_pipeline/references/` vẫn là T4 Milestone 1: lấy references từ Semantic Scholar API và metadata arXiv. `src/arxiv_pipeline/bibtex/` là Module 4 Milestone 2: trích xuất bibliography trực tiếp từ LaTeX/.bbl và chuẩn hóa citation key. Hai module có mục đích khác nhau, không ghi đè hoặc thay thế lẫn nhau.

### Hợp đồng dữ liệu

`GatheredSource` sử dụng các trường đầu vào sau (các trường không cần thiết có thể vắng mặt theo `TypedDict` hiện tại):

| Trường | Kiểu | Ý nghĩa |
|---|---|---|
| `arxiv_id` | `str` | ID bài arXiv |
| `version` | `int` | Số phiên bản |
| `root_file` | `str \| None` | File `.tex` gốc |
| `compilation_path` | `list[str]` | Thứ tự file trong luồng biên dịch |
| `merged_tex` | `str` | Nội dung LaTeX đã ghép |
| `bbl_files` | `list[str]` | Đường dẫn đến file `.bbl` nếu có |
| `status` | `str` | Trạng thái gathering |

Mỗi `BibtexEntry` có `arxiv_id`, `citation_key`, `entry_type`, `title`, `authors`, `year`, `doi`, `journal` và `url`. `PaperReferences` gồm `arxiv_id`, `unified_bibtex` và `citation_remap`.

### Cách sử dụng từ Python

Chạy từ thư mục gốc repository, với `src` nằm trong `PYTHONPATH`:

```python
from arxiv_pipeline.bibtex import (
    extract_and_unify_references,
    rewrite_citation_keys,
)

versions_sources = [
    {
        "arxiv_id": "2409.00001",
        "version": 1,
        "root_file": "main.tex",
        "compilation_path": ["main.tex"],
        "merged_tex": tex_v1,
        "bbl_files": ["/path/to/v1/main.bbl"],
        "status": "ok",
    },
    {
        "arxiv_id": "2409.00001",
        "version": 2,
        "root_file": "main.tex",
        "compilation_path": ["main.tex"],
        "merged_tex": tex_v2,
        "bbl_files": ["/path/to/v2/main.bbl"],
        "status": "ok",
    },
]

result = extract_and_unify_references(versions_sources)

# Bàn giao cho Module 5
bibtex_rows = result["unified_bibtex"]

# Bàn giao cho Module 2: lấy map đúng phiên bản trước khi rewrite
remap_v2 = result["citation_remap"].get(2, {})
rewritten_tex_v2 = rewrite_citation_keys(tex_v2, remap_v2)
```

Hai hàm public:

```python
extract_and_unify_references(
    versions_sources: list[GatheredSource]
) -> PaperReferences

rewrite_citation_keys(
    text: str,
    remap: dict[str, str]
) -> str
```

`extract_and_unify_references` sắp xếp nguồn theo số version trước khi xử lý. Nếu danh sách đầu vào rỗng, hàm trả hợp đồng rỗng. Nếu đầu vào trộn nhiều `arxiv_id` khác nhau, hàm báo `ValueError`. Những version không có bibliography vẫn được giữ trong `citation_remap` với map rỗng.

### Quy tắc trích xuất, hợp nhất và rewrite

1. Parser tìm môi trường `\\begin{thebibliography} ... \\end{thebibliography}` trong `merged_tex` và nội dung của các file `.bbl` tồn tại, sau đó tách từng `\\bibitem` thành citation key và raw entry.
2. Nếu cùng một citation key xuất hiện trong cả `.tex` và `.bbl` của cùng nguồn, parser chỉ giữ lần xuất hiện đầu tiên.
3. DOI được chuẩn hóa trước khi so khớp; title được chuẩn hóa về chữ thường, bỏ dấu và ký tự đặc biệt. Hai mục được coi là trùng nếu có cùng DOI **hoặc** cùng title chuẩn hóa. Nếu không có cả hai định danh, fallback dùng citation key.
4. Canonical key là key của mục được gặp đầu tiên theo thứ tự version tăng dần. Key của mục trùng ở phiên bản sau được ánh xạ về key canonical.
5. Khi hợp nhất, trường còn thiếu được bổ sung từ mục trùng; danh sách authors được union, không thêm tên đã có; `year` được bổ sung nếu mục canonical chưa có năm.
6. `rewrite_citation_keys` xử lý các key trong `\\cite{a,b}` và dạng có tối đa hai optional arguments như `\\cite[p. 4]{a,b}`. Key không có trong map được giữ nguyên.

### Kiểm thử

Cài dependencies của project nếu môi trường chưa có pytest, sau đó chạy từ thư mục gốc repository:

```bash
PYTHONPATH=src pytest -q tests/bibtex
```

Test Module 4 hiện có kiểm tra: tách nhiều `\\bibitem` (bao gồm label tùy chọn), dedup giữa các version, hợp nhất DOI/năm, canonical key và citation remap, rewrite nhiều key trong một lệnh `\\cite`, giữ key không được ánh xạ, input rỗng và version không có bibliography.

### Giới hạn và việc cần xác nhận trước khi tích hợp

Nội dung `\\bibitem` là văn bản tự do nên việc suy luận `title`, `authors`, `journal` và `entry_type` hiện dùng heuristic; không thể đảm bảo đúng tuyệt đối cho mọi định dạng bibliography. Cần kiểm tra thêm các trường hợp thực tế như tên tác giả nhiều dòng, title có dấu chấm, DOI bị xuống dòng và tên venue có dấu chấm. Hướng dẫn Module 4 cũng yêu cầu chạy thử trên ít nhất 10 bài có raw TeX trong corpus trước khi merge. **Đây là bước xác nhận trên dữ liệu thật, không được xem là đã hoàn tất chỉ vì các test offline chạy qua.**

Khi tích hợp, Module 1 cung cấp `GatheredSource`; Module 4 chuyển `citation_remap` theo đúng version cho Khang (Module 2) và chuyển `unified_bibtex` cho Trọng (Module 5). Không đưa bibliography vào cây hierarchy của Module 3; đề Milestone 2 quy định phần tài liệu tham khảo được xử lý riêng bởi Module 4.
