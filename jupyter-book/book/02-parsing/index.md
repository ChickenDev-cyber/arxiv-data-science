# Chương 2 — Milestone 2: Phân tích cú pháp phân cấp (Parsing)

> **Người phụ trách:** Huỳnh Nhi (`@TrinhNhi3011`)  
> **Reviewer:** Hoàng (`huynhhuyhoang2006@gmail.com`)

Chương 1 kết thúc với một kho nguồn TeX/BibTeX được lưu theo từng phiên bản của mỗi bài báo (mục 1.4), kèm metadata và lịch sử phiên bản (mục 1.3). Chương 2 mô tả cách biến kho nguồn này thành dữ liệu có cấu trúc: mỗi phiên bản của một bài được phân tích thành một cây phân cấp, nội dung được làm sạch và chuẩn hóa, các mục tài liệu tham khảo được đưa về BibTeX thống nhất, và phần trùng lặp giữa các phiên bản được gộp lại.

## 1. Đầu vào từ Milestone 1

- **Nguồn TeX/BibTeX** của từng phiên bản: chỉ gồm file `.tex` và `.bib`; các lệnh `\begin{figure}`, `\includegraphics` vẫn còn trong TeX dù file hình đã bị bỏ (mục 1.4).
- **Danh sách phiên bản** của mỗi bài, lấy từ metadata đã được xác minh (mục 1.3).
- **Phiên bản không có TeX** (`unsupported`: chỉ có PDF, DOCX hoặc gói không có `.tex`) vẫn có bản ghi rỗng. Theo đề, những phiên bản này không sinh phần tử nào nhưng phải được **ghi nhận là rỗng** và tính vào độ phủ phiên bản, không bị bỏ qua.

## 2. Đầu ra

Ba bảng theo đề, mô tả chi tiết ở mục 2.5:

- `elements`: mỗi phần tử khác nhau của một bài (qua mọi phiên bản) là một dòng;
- `hierarchy`: các cạnh cha–con của cây phân cấp, theo từng phiên bản;
- `bibtex`: các mục tài liệu tham khảo sau khi khử trùng lặp và hợp nhất.

## 3. Cách đọc chương này

| Mục | Nội dung |
|---|---|
| [2.1 Phân tích cú pháp phân cấp](01-phan-cap.md) | Nhận diện file gốc có `\documentclass`, đi theo `\input`/`\include`, dựng cây Document → Section → … → Sentence/Block Formula/Figure |
| [2.2 Chuẩn hóa và khử trùng lặp](02-chuan-hoa.md) | Làm sạch lệnh định dạng, chuẩn hóa toán, chuyển `\bibitem` sang BibTeX, khử trùng lặp mức tham chiếu và mức nội dung giữa các phiên bản |
| [2.3 Thống kê](03-thong-ke.md) | Tỷ lệ parse thành công, độ phủ phiên bản, số phần tử và số mục tham chiếu trước/sau khử trùng lặp |
| [2.4 Benchmark](04-benchmark.md) | Thời gian chạy toàn bộ, thời gian trung bình mỗi bài, RAM tối đa, dung lượng đầu ra trên Colab CPU |
| [2.5 Bảng dữ liệu](05-bang-du-lieu.md) | Cột, kiểu và quan hệ của ba bảng `elements`, `hierarchy`, `bibtex`, kèm ví dụ |

Các mục 2.1 đến 2.3 nối tiếp nhau theo thứ tự xử lý: dựng cây (2.1), chuẩn hóa và gộp (2.2), rồi đo kết quả (2.3). Người chỉ cần biết dạng dữ liệu đầu ra có thể đọc thẳng mục 2.5.

> [!NOTE]
> **Đang chờ code Milestone 2:** Chương 2 đang được viết song song với code Milestone 2. Các phần mô tả yêu cầu đã theo đúng đề; chi tiết cài đặt, ví dụ thật và số liệu sẽ được bổ sung khi code được bàn giao.
