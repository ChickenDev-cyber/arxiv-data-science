# 1.9 Hạn chế và cơ chế chạy tiếp

> **Người phụ trách:** Mẫn Nhi (`@NhiNgo49`)  
> **Reviewer:** Hoàng (`huynhhuyhoang2006@gmail.com`)

---

## 1. Hạn chế của nguồn dữ liệu và quá trình thu thập

1. **Phụ thuộc dịch vụ bên ngoài.** Discovery và ngày nộp v1 phụ thuộc arXiv API; metadata phụ thuộc OAI-PMH; references phụ thuộc Semantic Scholar. Lỗi mạng, HTTP 429, lỗi server hoặc payload thay đổi có thể làm một task thành `partial`/`failed`.
2. **Nguồn references không phải kho lưu trữ đầy đủ tuyệt đối.** T4 chỉ thu được những mục Semantic Scholar trả về tại thời điểm truy vấn. Số mục có arXiv ID là một tập con của `raw_references`; không nên diễn giải số này thành tổng số trích dẫn của bài báo.
3. **Metadata có thể thiếu.** Ngày nộp của một reference chỉ được bổ sung nếu arXiv API trả về ID tương ứng và trường `published` hợp lệ. Module ghi nhận trường thiếu thay vì suy đoán.
4. **Discovery cần xác minh đầy đủ.** ID không xuất hiện trong phản hồi chưa đầy đủ không được xem là không tồn tại. Điều này làm tăng request khi phải xác minh riêng các ID còn mơ hồ.
5. **Giới hạn trang và tốc độ API.** T4 mặc định giới hạn 50 trang mới trong một lượt và phải tuân thủ khoảng chờ/rate limit của từng nguồn. Một bài có nhiều references hoặc gặp 429 có thể cần nhiều lần chạy.
6. **Benchmark phải có phạm vi rõ ràng.** Thời gian, RAM và dung lượng phụ thuộc môi trường, cache mode, số bài và trạng thái mạng. Không dùng benchmark mock hoặc máy cá nhân thay cho kết quả Colab CPU chính thức nếu yêu cầu môn học quy định môi trường đó.

## 2. Cơ chế phục hồi

T5 quản lý trạng thái task bằng SQLite theo `run_id`. Runner dùng `OutputLock`, khôi phục trạng thái và artifact đã xuất, kiểm tra task có thể dùng lại, sau đó chỉ chạy lại task cần thiết. Các kết quả được xác nhận mới được ghi vào accepted results và event log.

T1 Discovery hỗ trợ checkpoint theo batch khi được runner truyền store. Các batch đã phân loại được giữ lại; batch chưa xác minh có thể tiếp tục mà không phải làm lại toàn bộ dải.

T4 References lưu checkpoint sau mỗi trang hợp lệ, cache ngày nộp arXiv và lịch chờ API. Khi khởi động lại cùng run, module đọc các trang đã lưu và tiếp tục từ offset tiếp theo. Nếu checkpoint bị hỏng hoặc không khớp owner/ID/page size/fields, module báo lỗi thay vì bỏ qua dữ liệu lỗi.

## 3. Hướng cải tiến

- Theo dõi tỷ lệ `unresolved`, `partial`, `failed` theo nguồn và theo run để xác định công đoạn thường bị gián đoạn.
- Lưu lại `run_id`, commit code, cấu hình dải, môi trường và cache mode cùng báo cáo benchmark để có thể tái lập.
- Chạy lại các task có lỗi retryable sau thời gian chờ phù hợp; không retry lỗi dữ liệu không hợp lệ hoặc ID sai.
- So sánh số lượng `raw_references`, `arxiv_reference_count` và `complete_reference_count` để thấy rõ phần dữ liệu bị loại hoặc thiếu metadata.
- Chỉ kết luận về hiệu năng toàn dải sau khi có run thật, đầy đủ phạm vi và benchmark đúng môi trường yêu cầu.

## 4. Những điều chưa được xác nhận trong dữ liệu gửi kèm

ZIP hiện có `statistics/baseline-001.json` và `benchmarks/baseline-001.json` đều đánh dấu `is_mock=true`; accepted results cũng chỉ chứa một số bản ghi mẫu. Vì vậy chưa thể dùng chúng để kết luận tỷ lệ crawl thật, số lượng references toàn dải hoặc hiệu năng chính thức. Không có dữ liệu run thật đầy đủ trong gói được cung cấp để lập bảng trước/sau resume có ý nghĩa thực nghiệm.

