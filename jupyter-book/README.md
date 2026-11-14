# Tệp nguồn Jupyter Book trong repository nộp bài

Thư mục này lưu bản sao nội dung từ [arvix-report](https://github.com/ChickenDev-cyber/arvix-report), để repository nộp bài chứa cả mã nguồn pipeline và các tệp dùng viết Jupyter Book. Repo báo cáo riêng tiếp tục được dùng để biên soạn và xuất bản tại https://chickendev-cyber.github.io/arvix-report/.

## Nội dung

- `book/`: các chương Markdown, hình ảnh và thư viện tạo bảng/biểu đồ.
- `scripts/`: các script chuẩn bị dữ liệu báo cáo.
- `data/`: các bảng CSV/JSON tổng hợp đang được sách sử dụng, không phải corpus TeX thô.
- `myst.yml`, `requirements.txt`, `reqs/`: cấu hình và dependencies để build sách.

Không sao chép môi trường ảo, cache, sản phẩm build hay workflow triển khai Pages. Cấu hình sách giữ nguyên thông tin repo và địa chỉ xuất bản gốc.

## Build tại máy local

Từ thư mục gốc của repository mã nguồn:

```bash
cd jupyter-book
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m jupyter_book build --html
```

Trên Windows PowerShell, thay lệnh kích hoạt bằng `.venv\Scripts\Activate.ps1`. HTML được tạo tại `_build/html/`. Có thể xem bằng:

```bash
python -m http.server 3000 --directory _build/html
```

Các tệp tổng hợp hiện có được giữ nguyên, không cần chạy lại script chuẩn bị chỉ để sao chép hoặc xem nội dung sách. Nếu muốn tạo lại dữ liệu từ kết quả pipeline, đọc [hướng dẫn tại repo báo cáo](https://github.com/ChickenDev-cyber/arvix-report#readme); với script Mẫn Nhi, chỉ rõ repo mã nguồn bằng `python scripts/prepare_mannhi.py --source-root ..` khi đang đứng trong `jupyter-book/`.



