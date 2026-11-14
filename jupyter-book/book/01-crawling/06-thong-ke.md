# 1.6 Thống kê

> **Người phụ trách:** Hoàng (`huynhhuyhoang2006@gmail.com`)  
> **Reviewer:** Huỳnh Nhi (`TrinhNhi3011`)

Tiểu mục này trình bày các số liệu thống kê tổng hợp của pipeline thu thập dữ liệu (Milestone 1) nhằm đánh giá mức độ hoàn thành nhiệm vụ, hiệu quả tối ưu lưu trữ nguồn TeX, và độ bao phủ của mạng lưới trích dẫn.

---

## 1. Bảng 5 chỉ số bắt buộc theo yêu cầu đề bài

Các chỉ số dưới đây được tổng hợp trực tiếp từ tập kết quả `accepted_results.jsonl` và `scope.json` của pipeline.

> [!NOTE]
> **Trạng thái dữ liệu hiện tại:** 🔴 **Mock Data (Dữ liệu mẫu thử nghiệm)**  
> Nhóm đang sử dụng bộ mẫu giả lập đầy đủ schema để kiểm thử và biên dịch sách. Khi pipeline crawl dữ liệu thật hoàn tất, toàn bộ bảng và biểu đồ sẽ được tự động cập nhật lại thông qua `scripts/prepare_hoang.py` mà không cần chỉnh sửa văn bản.

| STT | Chỉ số bắt buộc theo đề | Giá trị tính toán | Ghi chú & Phương pháp tính toán |
|:---:|:------------------------|:-----------------:|:--------------------------------|
| **1** | **Số bài crawl thành công** | **10 / 10 bài** (Tỷ lệ: **100.0%**) | Số bài hoàn thành trọn vẹn cả 4 công đoạn (Discovery $\to$ Metadata $\to$ Sources $\to$ References) trên tổng số bài phát hiện |
| **2** | **Kích thước trung bình mỗi bài (Trước lọc)** | **474.0 KB** (485,376 bytes) | Trung bình cộng tổng dung lượng giải nén thô của tất cả các phiên bản của mỗi bài (*xem Lưu ý 1*) |
| | **Kích thước trung bình mỗi bài (Sau lọc)** | **88.9 KB** (91,034 bytes) | Giảm **81.2%** sau khi loại bỏ hình ảnh và file nhị phân, bảo toàn 100% các file `.tex` và `.bib` |
| **3** | **Số reference trung bình mỗi bài** | **18.5 refs/bài** (Trích dẫn có arXiv ID) | Số reference thô trung bình thu thập từ Semantic Scholar API là **21.2 refs/bài** (*xem Lưu ý 2*) |
| **4** | **Tỷ lệ thành công crawl Reference Metadata** | **100.0%** (185 / 185 refs) | Tỷ lệ trích xuất thành công ngày nộp của bài được trích dẫn từ arXiv API trên tổng số arXiv references |
| **5** | **Phạm vi khảo sát (`scope`)** | **10 bài** (`2301.00001` – `2301.00010`) | Dải ID thử nghiệm thuộc danh mục `cs.DS` (Computer Science - Data Structures and Algorithms) |

---

## 2. Lưu ý về phương pháp tính toán và đối chiếu dữ liệu

Nhằm đảm bảo sự chính xác tuyệt đối giữa yêu cầu của đề bài và cấu trúc dữ liệu lưu trữ, nhóm làm rõ hai điểm khác biệt cốt lõi:

### Lưu ý 1: Kích thước trung bình mỗi bài vs mỗi phiên bản
- Trong schema thu thập, trường `summary.mean_extracted_bytes_before_filter` ban đầu phản ánh kích thước trung bình **trên từng phiên bản (per version)** tải về từ `export.arxiv.org`.
- Tuy nhiên, đề bài yêu cầu kích thước trung bình **trên mỗi bài báo (per paper)**. Một bài báo có thể có nhiều phiên bản ($v_1, v_2, \dots, v_k$). Do đó, nhóm tính toán bằng cách cộng dồn tất cả các phiên bản trước khi lấy trung bình:
  $$\text{RawBytes}(P_i) = \sum_{v \in \text{Versions}(P_i)} \text{extracted\_bytes}(v)$$
  $$\overline{\text{Size}} = \frac{1}{N} \sum_{i=1}^N \text{RawBytes}(P_i)$$
- Trong đó $\text{RawBytes}(P_i)$ là tổng dung lượng trước lọc của bài $P_i$, và $\overline{\text{Size}}$ là kích thước trung bình mỗi bài. Nhờ công thức này, số liệu báo cáo phản ánh đúng tổng chi phí lưu trữ của toàn bộ lịch sử bài báo.

### Lưu ý 2: Phân loại số lượng Reference
Semantic Scholar Graph API trả về hai lớp dữ liệu trích dẫn:
1. **Tổng trích dẫn thô (`s2_reference_count`):** Bao gồm toàn bộ các tài liệu tham khảo (sách, hội thảo, bài báo thuộc nhiều nguồn xuất bản khác nhau). Trung bình đạt **21.2 tài liệu/bài**.
2. **Trích dẫn có định danh arXiv (`arxiv_reference_count`):** Chỉ lọc các trích dẫn có chứa mã số arXiv hợp lệ. Đây là tập hợp được đưa vào công đoạn truy vấn metadata ngày nộp bài qua arXiv API, trung bình đạt **18.5 tài liệu/bài**.

---

## 3. Trực quan hóa so sánh kích thước trước và sau lọc

Việc lọc bỏ các tệp hình ảnh (`.png`, `.jpg`, `.pdf`, `.eps`) trong các gói mã nguồn TeX giúp tiết kiệm đáng kể không gian lưu trữ và băng thông truyền tải, trong khi vẫn bảo toàn đầy đủ các lệnh nhúng hình (`\includegraphics`, `figure`) để phục vụ phân tích cấu trúc ở Milestone 2.

:::{figure} ../figures/fig-1-6-size-comparison.png
:name: fig-1-6-size-comparison
:alt: Biểu đồ so sánh kích thước trước và sau khi lọc hình ảnh

Biểu đồ so sánh dung lượng gói nguồn trước và sau khi loại bỏ hình ảnh trên từng bài báo. Tỷ lệ tiết kiệm dung lượng trung bình đạt hơn 80%.
:::

---

## 4. Thống kê chi tiết theo từng người phụ trách dải (Owner)

| Thành viên (Owner) | Số bài phụ trách | Tỷ lệ thành công | Dung lượng sau lọc TB | Số ref arXiv TB |
|:-------------------|:----------------:|:----------------:|:---------------------:|:----------------:|
| **Minh Trọng** (`Trong`) | 3 bài | 100.0% | 92.3 KB | 17.0 refs/bài |
| **Phúc Khang** (`Khang`) | 3 bài | 100.0% | 90.0 KB | 17.7 refs/bài |
| **Huỳnh Nhi** (`HuynhNhi`) | 2 bài | 100.0% | 116.5 KB | 25.5 refs/bài |
| **Mẫn Nhi** (`ManhNhi`) | 2 bài | 100.0% | 44.5 KB | 7.0 refs/bài |
| **Toàn nhóm** | **10 bài** | **100.0%** | **88.9 KB** | **18.5 refs/bài** |

*(Dữ liệu chi tiết được xuất tự động bởi `scripts/prepare_hoang.py` và lưu trữ tại `data/hoang/statistics_summary.json`)*.
