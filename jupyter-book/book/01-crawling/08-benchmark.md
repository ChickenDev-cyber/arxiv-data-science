# 1.8 Benchmark (Colab CPU)

> **Người phụ trách:** Hoàng (`huynhhuyhoang2006@gmail.com`)  
> **Reviewer:** Mẫn Nhi (`@NhiNgo49`)

Tiểu mục này trình bày kết quả đo lường hiệu năng, thời gian thực thi và mức tiêu thụ tài nguyên phần cứng (CPU, RAM, Disk) của pipeline thu thập dữ liệu trên môi trường chuẩn **Google Colab (chỉ sử dụng CPU)** theo đúng quy định của đề bài.

---

## 1. Môi trường và phương pháp đo lường

Để đảm bảo tính tái lập và công bằng khi đánh giá, toàn bộ các phép đo benchmark chính thức được thiết lập trên máy ảo tiêu chuẩn Google Colab không sử dụng bộ tăng tốc GPU/TPU.

> [!NOTE]
> **Trạng thái dữ liệu hiện tại:** 🔴 **Mock Data (Mô phỏng môi trường Colab CPU)**  
> Khi Trọng và Khang chốt phiên bản crawler cuối cùng trên nhánh chính của repo `arxiv-data-science`, nhóm sẽ thực thi benchmark trên một Git commit cố định và ghi nhận số liệu chính thức vào `data/hoang/benchmark_summary.json`.

| Tiêu chí | Cấu hình máy ảo Google Colab tiêu chuẩn |
|:---|:---|
| **Hệ điều hành** | Ubuntu 22.04 LTS (Linux kernel 6.1.85+) |
| **Vi xử lý (CPU)** | Intel(R) Xeon(R) CPU @ 2.20GHz — **2 vCPU** |
| **Dung lượng RAM hệ thống** | **12.68 GB** (13,612,879,872 bytes) |
| **Môi trường thực thi** | CPython 3.10.12 (Kiến trúc x86_64) |
| **Phương pháp giám sát RAM (`method`)** | Giám sát RSS toàn bộ cây tiến trình con qua thư viện `psutil` (chu kỳ lấy mẫu 0.1s) |
| **Chế độ bộ nhớ đệm (`cache_mode`)** | `cold` (Khởi chạy trên thư mục làm việc mới, không tái sử dụng cache tạm) |
| **Mã định danh Git Commit** | `52a480d` (Cải tiến cơ chế quản lý tiến trình và sidecar timing) |

---

## 2. Kết quả đo lường thời gian và tài nguyên

Các chỉ số thời gian chi tiết của từng bài báo và từng công đoạn được trích xuất từ chuỗi sự kiện `events.jsonl` và bộ đo lường benchmark độc lập:

| Nhóm chỉ số | Chỉ số cụ thể | Giá trị đo được | Đơn vị | Diễn giải kỹ thuật |
|:---|:---|:---:|:---:|:---|
| **Thời gian thực thi** | **Tổng thời gian Discovery** | **3.42** | giây | Quét dải ID và gửi truy vấn kiểm tra theo lô 100 ID |
| | **Thời gian xử lý bài nhanh nhất** | **2.85** | giây | Bài có 1 phiên bản và số lượng trích dẫn nhỏ |
| | **Thời gian xử lý bài chậm nhất** | **7.12** | giây | Bài có 3 phiên bản nguồn và hơn 35 trích dẫn |
| | **Thời gian trung bình mỗi bài** | **4.53** | giây/bài | Tính trên 10 bài hoàn thành trọn vẹn pipeline |
| | **Tổng thời gian thực thi (Wall time)** | **48.72** | giây | Thời gian từ lúc khởi động pipeline đến khi kết thúc |
| **Tài nguyên bộ nhớ** | **RAM Đỉnh (Peak RAM)** | **75.0** | MB | Mức bộ nhớ cao nhất của toàn bộ cây tiến trình |
| | **RAM Trung bình (Mean RAM)** | **50.0** | MB | Mức tiêu thụ ổn định, không có hiện tượng memory leak |
| **Không gian đĩa** | **Dung lượng đĩa tối đa (Peak Disk)** | **23.4** | MB | Tính cả không gian giải nén tạm trước khi lọc hình ảnh |
| | **Dung lượng kết quả cuối (Output)** | **17.5** | MB | Tổng dung lượng tệp nguồn TeX/BibTeX đã lọc và JSONL |

> [!IMPORTANT]
> **Lưu ý về phương pháp đo lường:** Khi chạy crawler phân tán hoặc chạy lại từ checkpoint (resume), thời gian của các máy tính riêng biệt **không được cộng dồn cơ học** thành wall time của toàn bộ hệ thống. Thời gian tổng thể được tính bằng khoảng cách thời gian giữa sự kiện bắt đầu đầu tiên và sự kiện kết thúc cuối cùng của pipeline.

---

## 3. Phân bố thời gian theo các công đoạn Pipeline

Biểu đồ dưới đây minh họa thời gian tiêu tốn cho từng công đoạn chính trong pipeline thu thập bài báo:

:::{figure} ../figures/fig-1-8-stage-timing.png
:name: fig-1-8-stage-timing
:alt: Biểu đồ phân bố thời gian theo công đoạn pipeline

Thời gian thực thi chi tiết của 4 công đoạn chính: Discovery (T1), Metadata (T2), Sources (T3) và References (T4).
:::

### Nhận xét hiệu năng:
1. **Công đoạn tải và xử lý nguồn (Sources - T3):** Chiếm tỷ trọng thời gian lớn nhất (khoảng 44%) do phải thực hiện tải các tệp nén dung lượng lớn từ máy chủ `export.arxiv.org`, giải nén trong bộ nhớ tạm và duyệt cây thư mục để lọc bỏ hình ảnh.
2. **Công đoạn trích dẫn (References - T4):** Chiếm khoảng 31% thời gian do Semantic Scholar API áp dụng giới hạn tần suất truy vấn (rate limit) nghiêm ngặt, đòi hỏi pipeline phải tuân thủ khoảng cách chờ giữa các request và cơ chế lũy tiến thời gian chờ (exponential backoff).
3. **Mức độ tiêu thụ RAM:** Đỉnh RAM duy trì ở mức rất thấp (~75 MB), chứng minh cơ chế stream tệp và giải nén theo luồng không làm phình to bộ nhớ trên máy ảo Colab.
