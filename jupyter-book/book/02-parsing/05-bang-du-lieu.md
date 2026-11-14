# 2.5 Bảng dữ liệu (elements, hierarchy, bibtex)

> **Người phụ trách:** Huỳnh Nhi (`@TrinhNhi3011`)  
> **Reviewer:** Hoàng (`huynhhuyhoang2006@gmail.com`)

> [!NOTE]
> **Đang chờ code Milestone 2:** Tên cột và kiểu dữ liệu dưới đây theo đúng đề Milestone 2. Ví dụ bản ghi hiện là ví dụ minh họa dựa trên ví dụ của đề; bảng có lọc sinh từ dữ liệu thật sẽ được thêm khi code Milestone 2 xuất ra ba bảng này.

Milestone 2 xuất ba bảng. Mục này chỉ mô tả dạng của chúng và quan hệ giữa chúng; quá trình tạo ra nội dung đã được trình bày ở mục 2.1 và 2.2.

## 1. Bảng `elements`

Mỗi dòng là một phần tử **khác nhau** của một bài, tính trên mọi phiên bản. Một phần tử có nội dung giống hệt nhau (sau khi làm sạch) ở nhiều phiên bản chỉ chiếm một dòng.

| Cột | Kiểu | Ràng buộc | Ý nghĩa |
|---|---|---|---|
| `element_id` | string | non-null, khóa chính | Định danh phần tử, duy nhất trong bài |
| `arxiv_id` | string | non-null | Bài chứa phần tử |
| `kind` | string (dictionary) | | Loại phần tử, ví dụ `document`, `heading`, `sentence`, công thức khối, hình (bảng cũng được coi là hình) |
| `content` | string | | Nội dung văn bản sau khi làm sạch và chuẩn hóa |

Kiểu *dictionary* nghĩa là cột chỉ nhận một tập nhỏ giá trị lặp lại, nên Parquet lưu theo dạng từ điển cho gọn.

## 2. Bảng `hierarchy`

Mỗi dòng là một cạnh nút–cha trong cây của **một phiên bản**.

| Cột | Kiểu | Ràng buộc | Ý nghĩa |
|---|---|---|---|
| `arxiv_id` | string | non-null | Bài |
| `version` | int | | Phiên bản (1, 2, …) |
| `element_id` | string | non-null, khóa ngoại tới `elements` | Nút con |
| `parent_id` | string | non-null, khóa ngoại tới `elements` | Nút cha |

Vì phần tử giống nhau giữa các phiên bản dùng chung một `element_id`, cây của v1 và v2 có thể cùng trỏ tới một dòng trong `elements`. Muốn dựng lại cây của phiên bản N thì lọc `hierarchy` theo (`arxiv_id`, `version = N`) rồi nối với `elements` qua `element_id`. Một phiên bản không có TeX (mục 1.4) không có cạnh nào trong bảng này, nhưng vẫn được ghi nhận là phiên bản rỗng trong thống kê độ phủ (mục 2.3).

## 3. Bảng `bibtex`

Mỗi dòng là một mục tài liệu tham khảo của một bài **sau khi đã khử trùng lặp và hợp nhất trường** (mục 2.2).

| Cột | Kiểu | Ràng buộc | Ý nghĩa |
|---|---|---|---|
| `arxiv_id` | string | non-null | Bài trích dẫn |
| `citation_key` | string | non-null | Khóa trích dẫn đã chọn sau khi gộp |
| `entry_type` | string (dictionary) | | `article`, `inproceedings`, `misc`, … |
| `title` | string | | Tiêu đề |
| `authors` | list of strings | | Danh sách tác giả |
| `year` | int | nullable | Năm |
| `doi` | string | | DOI |
| `journal` | string | | Tạp chí hoặc hội nghị |
| `url` | string | | Đường dẫn |

Cặp (`arxiv_id`, `citation_key`) xác định duy nhất một dòng. Mọi lệnh `\cite{}` trong nội dung đã được đổi sang khóa này, nên khóa trong `content` của `elements` khớp được với `bibtex`.

## 4. Định danh `element_id`

Theo đề, `element_id` bắt đầu bằng **khóa của bài**, tiếp theo là một **phần phân biệt duy nhất trong bài**. Đề dùng ví dụ `2310-12345#root1`, `2310-12345#h1`, `2310-12345#s1`: phần trước dấu `#` là khóa bài, phần sau là loại nút và số thứ tự.

Ví dụ minh họa (theo ví dụ của đề) cho một bài có hai phiên bản, trong đó tiêu đề mục và câu đầu không đổi, còn v2 thêm một câu mới:

`elements`

| element_id | arxiv_id | kind | content |
|---|---|---|---|
| `2310-12345#root1` | `2310.12345` | document | |
| `2310-12345#root2` | `2310.12345` | document | |
| `2310-12345#h1` | `2310.12345` | heading | Introduction |
| `2310-12345#s1` | `2310.12345` | sentence | Câu không đổi giữa v1 và v2. |
| `2310-12345#s2` | `2310.12345` | sentence | Câu mới chỉ có ở v2. |

`hierarchy`

| arxiv_id | version | element_id | parent_id |
|---|---|---|---|
| `2310.12345` | 1 | `2310-12345#h1` | `2310-12345#root1` |
| `2310.12345` | 1 | `2310-12345#s1` | `2310-12345#h1` |
| `2310.12345` | 2 | `2310-12345#h1` | `2310-12345#root2` |
| `2310.12345` | 2 | `2310-12345#s1` | `2310-12345#h1` |
| `2310.12345` | 2 | `2310-12345#s2` | `2310-12345#h1` |

Mỗi phiên bản có nút gốc riêng (`root1`, `root2`), còn `h1` và `s1` chỉ xuất hiện một lần trong `elements` dù có mặt ở cả hai cây. Đây chính là kết quả của khử trùng lặp mức nội dung ở mục 2.2.

Trong ví dụ của đề, khóa bài dùng dấu gạch ngang (`2310-12345`) còn cột `arxiv_id` dùng dấu chấm (`2310.12345`). Dạng khóa chính thức sẽ theo code Milestone 2 khi được bàn giao.

## 5. Tài liệu tham khảo

- Đề bài Milestone 2. https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/parsing
- Apache Parquet. *Encodings: Dictionary Encoding*. https://parquet.apache.org/docs/file-format/data-pages/encodings/
