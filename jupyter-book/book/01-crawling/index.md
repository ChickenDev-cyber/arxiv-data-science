# Chương 1 — Milestone 1: Thu thập Dữ liệu (Crawling)

> **Người phụ trách:** Mẫn Nhi (`@NhiNgo49`)  
> **Reviewer:** Hoàng (`huynhhuyhoang2006@gmail.com`)

Chương 1 trình bày toàn bộ phương pháp, kiến trúc, kết quả thống kê và đo lường benchmark của hệ thống thu thập dữ liệu bài báo khoa học từ arXiv.

## Danh mục các tiểu mục

- **[1.1 Tổng quan và kiến trúc pipeline](01-tong-quan.md):** Mục tiêu, sơ đồ luồng dữ liệu T1 $\to$ T5, công cụ và thư viện tiêu chuẩn.
- **[1.2 Khám phá bài (Discovery)](02-discovery.md):** Chiến lược sinh dải ID, so sánh các phương án và cơ chế lọc xác minh theo lô.
- **[1.3 Metadata và lịch sử phiên bản](03-metadata.md):** Khai thác qua giao thức OAI-PMH arXivRaw, phân tách tác giả và chuẩn hóa trạng thái.
- **[1.4 Nguồn TeX/BibTeX và loại hình](04-nguon-tex.md):** Tải gói nguồn, lọc file nhị phân/hình ảnh, giữ nguyên cấu trúc TeX và xử lý an toàn.
- **[1.5 References](05-references.md):** Thu thập mạng lưới trích dẫn qua Semantic Scholar API, cơ chế phân trang và xử lý rate-limit.
- **[1.6 Thống kê](06-thong-ke.md):** Bảng 5 chỉ số bắt buộc theo yêu cầu đề bài, phân tích kích thước bài và độ phủ trích dẫn.
- **[1.7 Chất lượng dữ liệu và bảng dữ liệu](07-chat-luong.md):** Đánh giá tính toàn vẹn, phân biệt validation vs quality, và ánh xạ schema `papers`/`references`.
- **[1.8 Benchmark (Colab CPU)](08-benchmark.md):** Đo lường hiệu năng end-to-end trên môi trường tiêu chuẩn Google Colab CPU.
- **[1.9 Hạn chế và cơ chế chạy tiếp](09-han-che.md):** Checkpoint theo lô, phục hồi sau sự cố gián đoạn và định hướng cải tiến.
