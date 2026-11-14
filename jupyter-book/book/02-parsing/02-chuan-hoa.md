# 2.2 Chuẩn hóa và khử trùng lặp

> **Người phụ trách:** Huỳnh Nhi (`@TrinhNhi3011`)  
> **Reviewer:** Hoàng (`huynhhuyhoang2006@gmail.com`)

> [!NOTE]
> **Đang chờ code Milestone 2:** Mục này trình bày yêu cầu của đề và cách nhóm thực hiện từng bước. Tên hàm, ví dụ trước/sau lấy từ dữ liệu thật và các trường hợp chưa hỗ trợ sẽ được bổ sung khi code Milestone 2 được bàn giao. Các ví dụ dưới đây là ví dụ minh họa tự viết, không phải dữ liệu thu thập.

Sau khi mục 2.1 dựng được cây phân cấp cho từng phiên bản, nội dung của các nút vẫn còn nhiều chi tiết trình bày không mang nghĩa, cùng một công thức có thể được viết theo nhiều cú pháp, và tài liệu tham khảo có thể nằm ở `.bib` hoặc ngay trong `.tex`. Bước chuẩn hóa đưa mọi thứ về một dạng thống nhất. Sau đó bước khử trùng lặp gộp những gì giống nhau, ở **hai mức** và **giữa các phiên bản** của cùng một bài.

## 1. Làm sạch lệnh định dạng

Đề yêu cầu bỏ các lệnh định dạng không đóng góp vào ý nghĩa của văn bản. Ví dụ đề nêu là `\centering`, tùy chọn vị trí `[htpb]` của môi trường hình/bảng và `\midrule`. Các lệnh cùng loại gồm những lệnh chỉ điều khiển cách dàn trang như khoảng cách, ngắt trang, cỡ chữ.

Ví dụ minh họa:

```latex
% Trước
\begin{figure}[htbp]
  \centering
  \includegraphics[width=0.8\linewidth]{arch.png}
  \caption{Kiến trúc   mô hình.}
\end{figure}

% Sau
\begin{figure}
  \includegraphics{arch.png}
  \caption{Kiến trúc mô hình.}
\end{figure}
```

Lệnh `\includegraphics` và `\caption` được giữ vì cho biết nút này là một Figure và nội dung của nó. Khoảng trắng thừa cũng được gộp lại để bước so khớp toàn văn ở dưới không bị ảnh hưởng bởi cách xuống dòng.

## 2. Chuẩn hóa công thức toán

Cùng một công thức có thể được viết bằng nhiều cú pháp khác nhau. Đề yêu cầu đưa về một dạng thống nhất:

| Loại | Các cách viết gặp trong TeX | Dạng chuẩn |
|---|---|---|
| Toán trong dòng | `$...$`, `\(...\)`, `\begin{math}...\end{math}` | `$...$` |
| Toán khối | `$$...$$`, `\[...\]`, `\begin{displaymath}`, `\begin{equation*}` | `\begin{equation}...\end{equation}` |

Ví dụ minh họa: `\( E = mc^2 \)` trở thành `$E = mc^2$`, còn `\[ \sum_{i=1}^n x_i \]` trở thành `\begin{equation}\sum_{i=1}^n x_i\end{equation}`. Nội dung bên trong công thức được giữ nguyên.

## 3. Trích xuất tài liệu tham khảo: `\bibitem` → BibTeX

Một số bài không dùng file `.bib` mà viết danh mục tài liệu tham khảo trực tiếp trong `.tex` bằng môi trường `thebibliography` và các lệnh `\bibitem{key}`. Đề yêu cầu chuyển các mục này thành mục BibTeX chuẩn bằng công cụ lập trình như biểu thức chính quy.

```latex
% Trước (trong .tex)
\bibitem{vaswani17} A. Vaswani, N. Shazeer, et al.
  Attention is all you need. In NeurIPS, 2017.

% Sau (BibTeX)
@inproceedings{vaswani17,
  title  = {Attention is all you need},
  author = {A. Vaswani and N. Shazeer and others},
  year   = {2017},
  booktitle = {NeurIPS}
}
```

Khóa trích dẫn (`vaswani17`) được giữ nguyên để các lệnh `\cite{vaswani17}` trong văn bản vẫn trỏ đúng. Trường nào không tách được chắc chắn thì để trống thay vì đoán, theo cùng nguyên tắc "thà thiếu còn hơn sai" đã dùng ở mục 1.3.

Một lưu ý từ Milestone 1: file `.bbl` đã bị loại khi lọc nguồn (mục 1.4), nên bước này chỉ làm việc với `\bibitem` có trong `.tex` và các file `.bib`.

## 4. Khử trùng lặp mức tài liệu tham khảo

Trong một phiên bản, hoặc giữa các phiên bản của cùng một bài, nhiều khóa trích dẫn khác nhau có thể cùng chỉ một tài liệu. Ví dụ v1 dùng khóa `vaswani17`, còn v2 dùng `attention2017` cho cùng bài "Attention is all you need". Đề yêu cầu:

1. **Chọn một khóa duy nhất** cho mỗi tài liệu.
2. **Đổi tên** mọi lệnh `\cite{}` đang dùng các khóa còn lại sang khóa đã chọn.
3. **Hợp nhất trường** (lấy hợp các trường) của các mục trùng, thay vì giữ một mục và bỏ thông tin của các mục kia.

Ví dụ minh họa: mục ở v1 có `title`, `author`, `year`, còn mục ở v2 có `title`, `doi`. Sau khi gộp, mục duy nhất có đủ `title`, `author`, `year`, `doi`; mọi `\cite{attention2017}` được đổi thành `\cite{vaswani17}`.

Hai mục được coi là cùng một tài liệu dựa trên nội dung chứ không dựa vào khóa. Quy tắc so khớp cụ thể (ví dụ so tiêu đề sau khi chuẩn hóa chữ hoa/thường và dấu câu, hoặc so DOI) và cách chọn khóa giữ lại sẽ được ghi theo code khi bàn giao.

## 5. Khử trùng lặp mức nội dung giữa các phiên bản

Giữa hai phiên bản của một bài, phần lớn câu, công thức và hình thường không đổi. Đề yêu cầu: nếu nội dung văn bản của một phần tử **khớp hoàn toàn** giữa các phiên bản thì chỉ dùng **một định danh** (`element_id`) cho phần tử đó trong đầu ra.

Việc so khớp phải làm **sau** khi đã làm sạch và chuẩn hóa ở các bước trên. Nếu so trước khi làm sạch, hai câu chỉ khác nhau ở khoảng trắng, ở `\centering` hay ở cách viết `\(...\)` so với `$...$` sẽ bị coi là khác nhau, và lượng trùng lặp bị bỏ sót.

Kết quả của bước này thể hiện ở bảng `elements` và `hierarchy` (mục 2.5): bảng `elements` có một dòng cho mỗi phần tử khác nhau qua mọi phiên bản, còn bảng `hierarchy` vẫn ghi đủ cây của từng phiên bản bằng cách để nhiều phiên bản cùng trỏ tới một `element_id`. Số phần tử và số mục tham chiếu trước và sau khử trùng lặp được thống kê ở mục 2.3.

## 6. Tài liệu tham khảo

- Patashnik, O. (1988). *BibTeXing*. https://ctan.org/pkg/bibtex
- Lamport, L. (1994). *LaTeX: A Document Preparation System* (2nd ed.). Addison-Wesley.
- Đề bài Milestone 2. https://hcmus-cs-ta.github.io/lab-book/intro-ds/statement/parsing
