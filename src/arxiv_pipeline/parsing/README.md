# Chạy M2 trên Colab CPU và khôi phục checkpoint

Hướng dẫn cho từng thành viên xử lý phần corpus M1 của mình. Dùng bản code đã được nhóm chốt và ghi lại commit/code SHA thực chạy. Mẫu 1.000 bài dùng để kiểm chứng trước bàn giao; kết quả mẫu chưa thay thế số liệu trên đủ corpus của nhóm.

## Chuẩn bị

1. Chọn runtime **CPU**, mount Drive và xác nhận mount thành công.
2. Đưa code đã chốt lên Colab. Các lệnh bên dưới chạy từ thư mục repo, nơi có `src/arxiv_pipeline`.
3. Sao chép archive M1 của thành viên từ Drive về `/content`, rồi giải nén trên ổ local Colab. Trỏ `--corpus` vào thư mục chứa các publication có `metadata.json` và các thư mục phiên bản; giữ mọi phiên bản, kể cả phiên bản không có TeX. Chuẩn bị dữ liệu xong trước khi bắt đầu benchmark.
4. Dùng Python **3.11+** cho workflow checkpoint hiện tại. Pilot dùng Python 3.13.18, backend `rule`, psutil 5.9.5 và pyarrow 23.0.1. Cài dependencies trước khi đo:

```bash
python -m pip install psutil==5.9.5 pyarrow==23.0.1
```

Backend `rule` không cần model NLP. Workflow verified resume hiện hỗ trợ backend này. Các backend khác cần môi trường/model riêng và một phép đo fresh phù hợp.

Trong notebook, mount Drive bằng một cell tương tác:

```python
from google.colab import drive
drive.mount('/content/drive')
```

Hoặc từ terminal đã cài Colab CLI: `colab drivemount -s TEN_SESSION`.

Xác nhận mount trước khi tạo thư mục backup:

```python
import os
from pathlib import Path

assert os.path.ismount('/content/drive')
assert Path('/content/drive/MyDrive').is_dir()
```

## Chạy fresh và đo đồng thời

Ví dụ cho thành viên `trong`; thay đường dẫn tương ứng cho `khang`, `hoang`, `man-nhi` hoặc `huynh-nhi`. Mỗi member/run dùng một thư mục output và một đường dẫn backup riêng. Không cho hai runner cùng ghi một DB hoặc cùng backup path.

```bash
M2_CORPUS=/content/m1-input/corpus/raw/trong
M2_OUTPUT=/content/m2-trong-fresh-01
M2_BACKUP=/content/m2-trong-fresh-01-backup/checkpoint.sqlite

PYTHONPATH=src python -m arxiv_pipeline.parsing.benchmark \
  --corpus "$M2_CORPUS" --output-dir "$M2_OUTPUT" \
  --backend rule --parquet --checkpoint \
  --backup-db "$M2_BACKUP" --backup-every 1000
```

Thư mục `--output-dir` phải **chưa tồn tại**. Số 1.000 là chu kỳ khởi đầu đã dùng trong pilot; theo dõi `checkpoint_backup_seconds` khi DB lớn hơn để điều chỉnh. Checkpoint SQLite local được commit theo từng bài; backup local sao chép toàn bộ DB sau mỗi N bài mới và khi parser thoát có kiểm soát. Raw benchmark CLI trong ví dụ này không tự chuyển backup lên Drive: dùng notebook để lưu các generation bất biến. Không trỏ `--backup-db` trực tiếp vào Drive. Khi số bài vừa chia hết cho N, code hiện ghi snapshot ở mốc đó và thêm một lần lúc thoát.

Để ghi nhận code SHA trước chạy:

```bash
PYTHONPATH=src python -c 'from arxiv_pipeline.parsing.benchmark import source_fingerprint; print(source_fingerprint())'
```

Không thay code/input/backend trong lúc một lượt đang chạy. Các raw CLI flags ở `arxiv_pipeline.parsing.cli` có thể dùng để xử lý riêng; để lấy số đo end-to-end và RSS cần chạy qua `parsing.benchmark` như trên.

## Kiểm tra sau lượt chạy

Giữ đầy đủ:

- `benchmark.json`: trạng thái, code hash, Python/package versions, lệnh, wall-time, RSS và kích thước output.
- `pipeline.json`, `stdout.log`, `stderr.log`: số bài/phiên bản, errors và tiến độ.
- `parsing.sqlite`, `parquet/`: dữ liệu và ba bảng xuất.
- `statistics/publications.json`, `publications.csv`, `summary.json`: độ phủ từng publication và số lượng trước/sau dedup.
- Snapshot Drive và tệp `.sqlite.sha256` cùng generation; archive input/code và metadata môi trường đủ để phục hồi.

Một lượt đạt yêu cầu vận hành khi `benchmark.json` có `status=complete`, exit code 0, số input được xử lý/bỏ qua đúng scope, không có publication thất bại, đủ thống kê/version coverage và có Parquet. `complete` xác nhận workflow xử lý/đo hoàn tất; chất lượng hierarchy và reference vẫn cần được audit.

Một version không có TeX được ghi `empty`, không tự gọi là submission PDF-only upstream. Tỷ lệ parse thực và tỷ lệ phiên bản được ghi nhận là hai chỉ số riêng. Số BibTeX trước dedup là số occurrence eligible trước gộp khóa; không suy từ số citation remap.

## Khi process hoặc VM gián đoạn

Giữ generation có `complete.json`, `checkpoint.sqlite`, `.sqlite.sha256` và `run.json`. Notebook tự tải và kiểm snapshot trên ổ local trước restore; raw CLI cần chuẩn bị một bản local đã xác minh trước khi gọi benchmark. Không sao chép cặp tệp giữa lúc runner đang thay snapshot; nếu hash không khớp, giữ bản nguồn để kiểm tra và dùng generation hoàn tất đã được xác minh.

Khôi phục input trên ổ local, dùng đúng code, Python/resources và backend như checkpoint gốc. Có thể thay đường dẫn cấp corpus khi giữ nguyên tên thư mục từng bài, nội dung và cấu trúc phiên bản. Công cụ đối chiếu input/code/config/output trước khi bỏ qua từng bài; code thay đổi sẽ làm các bài tương ứng được tính lại. Không ép bỏ qua khi fingerprint không khớp.

Resume vào **output mới**, giữ snapshot nguồn và ghi backup tiếp theo ra một tên khác:

```bash
M2_CORPUS=/content/m1-input/corpus/raw/trong
M2_RESTORED_OUTPUT=/content/m2-trong-resume-02
M2_SNAPSHOT=/content/m2-restored/checkpoint.sqlite
M2_NEXT_BACKUP=/content/m2-trong-resume-02-backup/checkpoint.sqlite

PYTHONPATH=src python -m arxiv_pipeline.parsing.benchmark \
  --corpus "$M2_CORPUS" --output-dir "$M2_RESTORED_OUTPUT" \
  --backend rule --parquet --resume-from "$M2_SNAPSHOT" \
  --backup-db "$M2_NEXT_BACKUP" --backup-every 1000
```

Kiểm `processed_publications` và `skipped_publications`. Một lượt chỉ kiểm lại toàn bộ checkpoint có thể có 0 bài tính mới; thời gian này không phải thời gian parse mới corpus. Raw `pipeline.json` có một số trường legacy; dùng cách tính và scope trong `benchmark.json` khi báo cáo.

Nếu VM còn sống mà process chết, DB local có thể mới hơn snapshot Drive; giữ DB cùng các sidecar WAL/SHM và kiểm SQLite trước khi trích snapshot nhất quán bằng backup API. Không lấy riêng tệp DB đang hoạt động làm snapshot. Nếu mất VM thì chỉ có dữ liệu đã được lưu ra ngoài runtime.

Ctrl+C của benchmark gửi SIGINT cho parser và chờ tối đa 10 giây trước khi buộc dừng. Backup Drive lớn có thể vượt khoảng này, vì vậy phải kiểm tra snapshot đã hoàn tất thay vì giả định lần backup cuối luôn thành công. Không xóa output/Drive backup chỉ để chạy lại lệnh khi chưa xem lỗi.

## Diễn giải số đo cho báo cáo

- Wall chính: spawn parser tới exit, gồm imports, processing, checkpoint/backup trong parser, SQLite close và Parquet export. `checkpoint_backup_seconds` nằm trong wall, được ghi thêm để phân tích chi phí.
- RAM: đỉnh RSS cây tiến trình supervisor + parser lấy mẫu mỗi 50 ms. Không dùng RAM toàn máy hoặc trường Python heap `peak_ram_mb` cũ. Khi tắt tracemalloc, log legacy có thể in `RAM Peak: 0.00 MB`; đó không phải số RSS.
- Storage: báo riêng SQLite và Parquet, rồi tổng nếu giữ cả hai. Ghi riêng backup và archive; không cộng tất cả bản sao vào dung lượng ba bảng dataset.
- Staging/install/model download, restore trước lượt, export thống kê sau parser và transfer sau lượt nằm ngoài timer; cache không được flush thì không gọi là cold-cache.
- Giữ riêng số đo fresh và resume của từng runtime/invocation. Không cộng wall năm máy thành wall của một lượt toàn nhóm; trình bày phần việc, phiên bản và giới hạn từng phép đo.

AMSRefs được hỗ trợ cho các field braced thông dụng, gồm author lặp và date/eprint. Parser không phải bộ thực thi TeX đầy đủ; custom macros, dữ liệu M1 thiếu và các citation/cleanup candidates vẫn cần được ghi rõ trong audit. Giữ metadata không suy luận được ở trạng thái rỗng/nullable.

## Notebook bàn giao cho thành viên

Dùng [m2_colab_runbook.ipynb](../../m2_colab_runbook.ipynb), mặc định `CODE_MODE='commit'`. Trưởng nhóm tự commit/push đầy đủ code đã kiểm tra rồi lấy SHA:

```bash
git rev-parse HEAD
```

Điền SHA 40 ký tự đó vào `COMMIT_SHA` trong notebook trước khi gửi cho thành viên. Notebook clone repository GitHub, checkout detached đúng commit, kiểm HEAD và `EXPECTED_SOURCE_SHA256` rồi mới xử lý. Không cần ZIP code trên Drive. Không dùng tên nhánh `main` hoặc `HEAD` thay SHA; không tự pull code giữa lượt. `run.json` và mỗi snapshot ghi commit thực chạy để phục hồi cùng phiên bản.

`COMMIT_SHA` để trống cho tới khi người dùng commit/push; không tự điền commit cũ vì nó chưa chứa bản sửa local. Nếu chỉ thay notebook/tài liệu, fingerprint package giữ nguyên. Nếu sửa package Python, cần cập nhật fingerprint và kiểm chứng lại trước khi bàn giao.

Chế độ `CODE_MODE='bundle'` và `tools/build_m2_release.py` vẫn giữ làm tùy chọn để phục hồi theo code ZIP cũ. Luồng clone thông thường không cần chạy công cụ tạo ZIP.

Mỗi người chọn `OWNER`, `RUN_MODE`, `RUN_ID`. `fresh` dùng RUN_ID mới. `resume` dùng RUN_ID mới khác lượt nguồn và điền `RESUME_FROM` đến checkpoint Drive cũ. Notebook từ chối xóa/ghi đè output đã có và ghi snapshot mới sang thư mục run mới. Archive M1 được hash; resume kiểm owner/code/Python/input checksum trước chạy. Giữ commit gốc; nếu checkpoint cũ dùng bundle thì giữ bundle đó để khôi phục, không tự pull code mới.

Các file kết quả nằm trong `milestone 2/{OWNER}/{RUN_ID}/artifacts/result-.../`, gồm `parquet/`, SQLite, logs, `statistics/`, JSON benchmark. `handoff.json` ở thư mục run trỏ tới generation đã kiểm định và ghi checksum đọc lại từ Drive. Snapshot mỗi 1.000 bài chỉ bảo vệ tiến độ đến generation hoàn tất gần nhất; không có bảo đảm an toàn tuyệt đối khi runtime mất. Tệp snapshot và checksum cần cùng generation.

M2 hiện yêu cầu link code và Jupyter Book theo nhóm, không yêu cầu dataset Hugging Face cá nhân. Ba bảng xử lý được giữ để dùng cho M3.


## Sao lưu sau sự cố checkpoint ngày 18/11/2026

Notebook mới dùng `colab.run_prepared`: parser chỉ backup ở `LOCAL_WORK_DIR/checkpoints/`; guardian trong notebook lấy cặp DB/hash khớp, đóng băng bản local rồi chép vào `snapshots/snapshot-.../` mới. Guardian tải bản Drive về một file local độc lập, kiểm SHA-256, SQLite integrity và số publication/checkpoint, chép run identity kèm hash và ghi `complete.json` cuối cùng. Không mở SQLite trên Drive; generation cũ được giữ nguyên. Nếu gặp lỗi truyền hoặc backup đang thay đổi thì thử lại ở lần poll sau; checkpoint local có thể mới hơn Drive và một số mốc trung gian có thể không được publish.

Khi benchmark thoát, `finalize_run` lưu mọi artifact hiện có **trước** bước kiểm định. `artifacts.json` chỉ xác nhận transport và ghi cả lỗi copy từng file. `handoff.json` chỉ được ghi khi benchmark/coverage/Parquet và snapshot cuối hợp lệ, đồng thời SQLite kết quả đã được tải lại từ Drive để kiểm toàn vẹn. Lỗi kiểm định không ngăn lưu dữ liệu/log để phục hồi. Mỗi lần thử sync tạo generation mới, không ghi đè bản đã lưu.

Guardian nằm ngoài cây tiến trình benchmark: wall/RSS chính đo parser và backup local; truyền Drive đồng thời có thể cạnh tranh CPU/IO nhưng RSS của guardian không nằm trong số đo. Chi phí `checkpoint_backup_seconds` chỉ là backup local, không phải tổng chi phí truyền Drive. Báo rõ giới hạn này và không gọi wall trừ backup là thời gian parser thuần. Việc đọc lại trên cùng mount chưa chứng minh dữ liệu bền qua một VM mới; nên giữ thêm bản kết quả trên máy local.

Bản mới thay đổi source fingerprint. Muốn resume checkpoint cũ, giữ đúng commit hoặc bundle cũ và Python/input gốc; không sửa checksum/code hash để ép bỏ qua. Lượt recovery 5.000 bài của Trọng và hai phép đo fresh/resume gốc được giữ nguyên, không bị thay thế bằng smoke test của notebook mới.
