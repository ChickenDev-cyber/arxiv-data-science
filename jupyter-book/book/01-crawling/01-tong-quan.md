# 1.1 Tổng quan và kiến trúc pipeline

> **Người phụ trách:** Mẫn Nhi (`@NhiNgo49`)  
> **Reviewer:** Hoàng (`huynhhuyhoang2006@gmail.com`)

---

## 1. Mục tiêu

Milestone 1 xây dựng pipeline thu thập thông tin bài báo arXiv và các dữ liệu liên quan. Theo cấu trúc source hiện tại, pipeline có bốn công đoạn nghiệp vụ:

1. **Discovery (T1):** tạo ID ứng viên trong dải được giao, xác minh ID bằng arXiv API và tạo danh sách bài tìm được.
2. **Metadata (T2):** lấy tiêu đề, tác giả, ngày nộp lần đầu, lịch sử phiên bản và venue từ OAI-PMH arXivRaw.
3. **Sources (T3):** tải source của từng phiên bản, nhận diện định dạng và trích xuất các tệp TeX/BibTeX theo quy tắc của module.
4. **References (T4):** lấy danh sách tài liệu tham khảo từ Semantic Scholar, chuẩn hóa ID arXiv và bổ sung ngày nộp từ arXiv API.

T5 không phải một nguồn dữ liệu thứ năm. Đây là lớp điều phối các công đoạn, quản lý task, trạng thái, checkpoint, phục hồi và xuất kết quả.

## 2. Kiến trúc tổng thể

```{mermaid}
flowchart TD
    A["Cấu hình dải ID<br/>config.py"] --> B["T1 Discovery<br/>arXiv Atom API"]
    B --> C["Danh sách PaperTask"]
    C --> D["T5 PipelineRunner"]
    D --> E["T2 Metadata<br/>OAI-PMH arXivRaw"]
    D --> F["T3 Sources<br/>arXiv source endpoint"]
    D --> G["T4 References<br/>Semantic Scholar"]
    G --> H["arXiv API<br/>ngày nộp v1"]
    E --> I["Result theo schema 1.2"]
    F --> I
    G --> I
    B --> I
    I --> J["Validation + SQLite state"]
    J --> K["accepted_results.jsonl<br/>events / corpus / reports"]
```

Sơ đồ thể hiện quan hệ module ở mức chức năng. Khi chạy T5, `PipelineRunner` điều phối các stage theo thứ tự: discovery luôn chạy trước; stage `sources` gọi T3; stage `references` gọi T4. T2 được tích hợp vào luồng task metadata và được runner phục hồi/lưu trạng thái cùng các task khác. Các module nghiệp vụ trả về `Result`; runner kiểm tra kết quả rồi mới ghi nhận và xuất artifact.

## 3. Công nghệ và thư viện

| Thành phần | Vai trò trong dự án | Cách sử dụng |
|---|---|---|
| Python 3.10+ | Ngôn ngữ triển khai pipeline | Module và CLI trong `src/arxiv_pipeline/` |
| arXiv Atom API | Xác minh ID ứng viên ở T1; lấy ngày nộp v1 ở T4 | Gửi HTTP request và phân tích Atom XML |
| OAI-PMH arXivRaw | Lấy metadata và ngày của từng phiên bản ở T2 | `GetRecord` với `metadataPrefix=arXivRaw` |
| arXiv source endpoint | Tải gói source của từng phiên bản ở T3 | Download và trích xuất định dạng source được hỗ trợ |
| Semantic Scholar Graph API | Lấy references ở T4 | Phân trang qua trường `next` |
| `urllib`, `xml.etree.ElementTree`, `json`, `pathlib` | HTTP, XML, JSON và thao tác tệp | Thư viện chuẩn Python |
| SQLite | Trạng thái task và checkpoint của runner | `state.sqlite` theo `run_id` |

Các module HTTP trong repository sử dụng thư viện chuẩn Python, trong đó có `urllib`; không nên mô tả dự án là dùng `requests`. `matplotlib` chỉ được dùng ở phần sinh biểu đồ báo cáo nếu được khai báo trong môi trường của repo sách, không phải thư viện bắt buộc của pipeline crawl.

## 4. Cấu trúc module

| Module | File chính | Trách nhiệm |
|---|---|---|
| Hợp đồng dữ liệu | `contracts.py`, `pipeline_contracts.py` | Schema `Result`, `PaperTask`, `Scope`, `Statistics`, `Benchmark` và các protocol dùng chung |
| Discovery | `discovery/service.py`, `discovery/api.py` | Sinh ID, chia batch, xác minh Atom feed và tạo Result |
| Metadata | `metadata/service.py`, `metadata/api.py` | Đọc OAI-PMH, kiểm tra trường metadata và lịch sử phiên bản |
| Sources | `sources/service.py`, `sources/extractor.py`, `sources/api.py` | Tải source, nhận diện định dạng, giải nén và lọc tệp |
| References | `references/service.py`, `references/api.py`, `references/checkpoint.py` | Phân trang S2, chuẩn hóa references, lấy ngày arXiv và resume |
| Runner | `runner/service.py`, `runner/state.py`, `runner/checkpoints.py` | Điều phối stage, lưu trạng thái, phục hồi và xuất kết quả |
| Validation | `validation/` | Kiểm tra Result, artifact, scope và corpus |

## 5. Dữ liệu đầu ra

Các module dùng chung schema phiên bản `1.2` trong `contracts.py`. Một Result chứa các thông tin chính:

- `run_id`, `owner`, `is_mock`: nhận diện lượt chạy, thành viên phụ trách và dữ liệu thật/mô phỏng.
- `step`, `arxiv_id`, `version`: xác định công đoạn và bài/phiên bản liên quan.
- `status`: kết quả xử lý như `success`, `partial`, `empty`, `unsupported`, `not_found` hoặc `failed`.
- `data`, `missing_fields`, `error`: dữ liệu trả về, trường còn thiếu và lỗi nếu có.
- `provenance`, `metrics`, `duration_seconds`, `attempts`: nguồn gốc, số đo và thông tin thực thi.

Không được gộp `partial` với `success`: một Result `partial` có thể chứa dữ liệu đã lấy được nhưng vẫn còn ID chưa xác minh, trang chưa tải hoặc trường metadata còn thiếu. Tương tự, `empty` ở T4 biểu thị không có reference arXiv hợp lệ sau khi xử lý, không đồng nghĩa với lỗi truy cập.

## 6. Phạm vi và tính trung thực của số liệu

Số liệu trong báo cáo phải gắn với `run_id`, owner và cờ `is_mock`. Các file `statistics/*.json`, `benchmarks/*.json` và `runs/<run_id>/accepted_results.jsonl` trong bản repository được cung cấp có chứa dữ liệu mẫu `is_mock=true`; chúng hữu ích để kiểm tra luồng và dựng biểu đồ, nhưng không đại diện cho kết quả crawl thật toàn dải. Chỉ thay nhãn thành dữ liệu thật khi manifest của run xác nhận `is_mock=false`.
