# Mẫn Nhi — Module 4 (Milestone 2)

## Mục tiêu

Nhận danh sách `GatheredSource` theo các phiên bản của cùng một bài arXiv; đọc `thebibliography` từ `merged_tex` và các file `.bbl`; chuyển mỗi `\\bibitem` thành `BibtexEntry`; gộp tham khảo trùng nhau; tạo `citation_remap` để Module 2 thay key trong `\\cite{...}`.

## Phần đã bổ sung

`src/arxiv_pipeline/bibtex/`

- `contracts.py`: TypedDict cho `GatheredSource`, `BibtexEntry`, `PaperReferences`.
- `parser.py`: tìm `thebibliography`, tách từng `\\bibitem`, đọc `.bbl`, bóc tách các trường.
- `normalizer.py`: chuẩn hóa DOI và title.
- `deduplicator.py`: đối sánh DOI **hoặc** title đã chuẩn hóa; hợp nhất trường, giữ canonical key đầu tiên theo thứ tự version.
- `remapper.py`: đổi key trong `\\cite{a,b}` và giữ optional arguments.
- `service.py`: hàm public `extract_and_unify_references(...)`.

## Cách dùng

```python
from arxiv_pipeline.bibtex import (
    extract_and_unify_references,
    rewrite_citation_keys,
)

result = extract_and_unify_references([
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
])

# M4 -> M5
bibtex_rows = result["unified_bibtex"]

# M4 -> M2: select the mapping for the current version
remap_for_v2 = result["citation_remap"].get(2, {})
cleaned_tex_v2 = rewrite_citation_keys(tex_v2, remap_for_v2)
```

## Quy tắc xử lý

1. DOI được chuẩn hóa (bỏ `https://doi.org/`, `doi:` và dấu câu cuối).
2. Title được chuẩn hóa về lowercase, bỏ dấu và ký tự đặc biệt.
3. Hai mục được coi là trùng nếu cùng DOI **hoặc** cùng title chuẩn hóa.
4. Canonical key là key xuất hiện đầu tiên khi duyệt version tăng dần; ví dụ v1 `refA`, v2 `refB` thì v2 map `refB -> refA`.
5. Khi gộp, trường thiếu được bổ sung; `authors` được union không lặp; `year` chỉ lấy giá trị có nếu bản canonical chưa có.
6. Key không tìm thấy trong map được giữ nguyên khi rewrite.
7. Bài PDF-only hoặc không có bibliography trả về danh sách rỗng và map rỗng cho version đó (không tự tạo BibTeX giả).

## Kiểm thử

Từ thư mục gốc repository:

```bash
PYTHONPATH=src pytest -q tests/bibtex
```

Các test hiện có kiểm tra: tách nhiều `\\bibitem`, gộp qua version bằng DOI/title, union thông tin, remap nhiều key trong một `\\cite`, giữ key lạ và input rỗng.

**Trước khi merge:** chạy thêm trên ít nhất 10 bài mẫu trong `data/` theo Definition of Done của nhóm. ZIP hiện tại không có thư mục `data/` chứa raw TeX mẫu, nên test 10 bài thật chưa thể xác nhận từ archive này. Cần dùng corpus đã tải của nhóm.

## Giới hạn cần review cùng Leader

`\\bibitem` là văn bản tự do, không có schema bắt buộc. Việc bóc `title`, `authors`, `journal`, `entry_type` trong parser hiện là heuristic; cần review các trường hợp bibliography thực tế khó (tác giả nhiều dòng, title có dấu chấm, DOI bị ngắt dòng, tên venue có dấu chấm). Không nên coi mọi trường được suy đoán là chính xác tuyệt đối. Nếu gặp ca khó, lưu raw example và thống nhất quy tắc với Trọng.

## Không thay thế module cũ

Giữ nguyên `src/arxiv_pipeline/references/` vì đó là T4 Milestone 1 (Semantic Scholar/API). Module mới nằm riêng ở `src/arxiv_pipeline/bibtex/` để tránh phá pipeline cũ.
