# Báo cáo Khai thác & Phân tích Dữ liệu arXiv (Milestone 1–2)

> **Môn học:** Nhập môn Khoa học Dữ liệu — Trường Đại học Khoa học Tự nhiên, ĐHQG-HCM  
> **Nhóm thực hiện:** ChickenDev  
> **Kho lưu trữ mã nguồn:** [ChickenDev-cyber/arxiv-data-science](https://github.com/ChickenDev-cyber/arxiv-data-science)  
> **Kho lưu trữ báo cáo:** [ChickenDev-cyber/arvix-report](https://github.com/ChickenDev-cyber/arvix-report)

---

## 1. Giới thiệu tổng quan

Báo cáo này tài liệu hóa toàn bộ quá trình thực hiện đồ án môn học Nhập môn Khoa học Dữ liệu, tập trung vào hai cột mốc chính:

1. **Chương 1 — Milestone 1: Thu thập Dữ liệu (Crawling)**
   - Hệ thống pipeline tự động thu thập bài báo arXiv theo các dải ID được chỉ định.
   - Trích xuất và xác thực metadata đa phiên bản qua OAI-PMH (arXivRaw).
   - Tải về và lọc gói nguồn TeX/BibTeX (loại bỏ ảnh và file nhị phân để tối ưu lưu trữ, giữ nguyên cấu trúc lệnh TeX).
   - Khai thác mạng lưới trích dẫn (References) thông qua Semantic Scholar API kèm cơ chế rate-limit bền bỉ.
   - Thống kê chi tiết, đánh giá chất lượng dữ liệu và đo lường tài nguyên (Benchmark trên Colab CPU).

2. **Chương 2 — Milestone 2: Phân tích Cú pháp Phân cấp (Parsing)**
   - Dựng cây cú pháp phân cấp: `Document` $\to$ `Section` $\to$ `Subsection` $\to$ `Paragraph` $\to$ `Sentence` / `Block Formula` / `Figure`.
   - Chuẩn hóa lệnh định dạng, công thức toán inline/block, và chuyển đổi `\bibitem` sang BibTeX.
   - Khử trùng lặp thực thể tham chiếu và phiên bản bài báo.
   - Xuất dữ liệu có cấu trúc phục vụ phân tích khoa học dữ liệu.

---

## 2. Danh sách thành viên và phân công

| STT | Thành viên | Vai trò chính trong Báo cáo | Phạm vi phụ trách |
|:---:|------------|-----------------------------|-------------------|
| 1 | **Huy Hoàng** | Quản trị hạ tầng sách, Thống kê & Benchmark | Hạ tầng repo, CI/CD Pages, `common.py`, Mục 1.6, 1.8, 2.3, 2.4 |
| 2 | **Mẫn Nhi** | Tổng quan kiến trúc, Discovery & References | Mục 1.1, 1.2, 1.5, 1.9, 2.1, README repo, Checklist nộp bài |
| 3 | **Huỳnh Nhi** | Metadata, Nguồn TeX, Chất lượng dữ liệu & Schema | Mục 1.3, 1.4, 1.7, 2.2, 2.5 |
| 4 | **Minh Trọng** | Kỹ sư Pipeline & Runner | Phát triển crawler, tối ưu hóa tốc độ và bộ nhớ |
| 5 | **Phúc Khang** | Kỹ sư Pipeline & Runner | Phát triển crawler, kiểm thử và cải tiến pipeline |

---

## 3. Cấu trúc mục lục

Sách được cấu trúc thành **đúng 2 chương** theo yêu cầu của đề bài:

```text
Jupyter Book
├── Chương 1 — Milestone 1: Crawling
│   ├── 1.1 Tổng quan và kiến trúc pipeline
│   ├── 1.2 Khám phá bài (discovery)
│   ├── 1.3 Metadata và lịch sử phiên bản
│   ├── 1.4 Nguồn TeX/BibTeX và loại hình
│   ├── 1.5 References
│   ├── 1.6 Thống kê
│   ├── 1.7 Chất lượng dữ liệu và bảng dữ liệu (papers, references)
│   ├── 1.8 Benchmark (Colab CPU)
│   └── 1.9 Hạn chế và cơ chế chạy tiếp
└── Chương 2 — Milestone 2: Parsing
    ├── 2.1 Phân tích cú pháp phân cấp
    ├── 2.2 Chuẩn hóa và khử trùng lặp
    ├── 2.3 Thống kê
    ├── 2.4 Benchmark
    └── 2.5 Bảng dữ liệu (elements, hierarchy, bibtex)
```

> [!TIP]
> Tất cả các biểu đồ, bảng dữ liệu và số liệu trong sách đều được **sinh tự động từ mã nguồn và dữ liệu kết quả khi build sách**, đảm bảo tính trung thực học thuật và khả năng tái lập (reproducibility).
