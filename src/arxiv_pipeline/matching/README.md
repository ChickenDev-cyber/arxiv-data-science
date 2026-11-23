# M3 — Module E: pipeline và dataset delivery

Nhánh phát triển: `feat/m3-pipeline-delivery`. Module E thuộc Trọng; các module
`data`, `labeling`, `modeling`, `evaluation`, `validation` do A–D bàn giao riêng.
Không có implementation giả của các module này trong source.

## Chạy local

```bash
python -m pip install -r src/requirements-m3.txt
PYTHONPATH=src python -m arxiv_pipeline.matching --config /path/run.json --preflight
PYTHONPATH=src python -m arxiv_pipeline.matching --config /path/run.json
```

Notebook CPU: `src/m3_colab_runbook.ipynb`. Điền commit đã push, đường dẫn input
chung và thư mục output trên Drive trước khi chạy. Notebook không tự upload HF.

## Inventory v1 — contract với Huỳnh Nhi

```json
{
  "schema_version": 1,
  "owners": {
    "trong": {
      "N": 5000,
      "snapshot": "official-input-snapshot",
      "tables": {
        "papers": ["inputs/trong/papers/part-00000.parquet"],
        "references": ["inputs/trong/references/part-00000.parquet"],
        "elements": ["inputs/trong/elements/part-00000.parquet"],
        "hierarchy": ["inputs/trong/hierarchy/part-00000.parquet"],
        "bibtex": ["inputs/trong/bibtex/part-00000.parquet"]
      },
      "row_counts": {},
      "warnings": []
    }
  }
}
```

Khai báo tất cả owner tham gia mô hình chung. Đường dẫn shard tương đối với file
inventory hoặc tuyệt đối; không phải glob. `N` phải bằng số paper ID chính thức
phân biệt của owner. Các shard `papers` xác định owner của bài và không trộn
owner; các bảng khác có thể trộn owner, được lọc theo tập ID trong papers.
Các shard phải có đủ cột và đúng thứ tự schema, kể cả bảng rỗng.
`row_counts` khai báo theo từng owner, không theo tổng dòng của shard trộn owner.
Mọi count được cung cấp đều phải khớp dữ liệu thật; input bàn giao chính thức
nên khai báo đủ5 bảng để phát hiện shard thiếu dòng. Ví dụ rút gọn để map rỗng.

Schema xuất bản được định nghĩa bằng Arrow trong `contracts.SCHEMAS`; đọc từ
đó thay vì tự suy luận kiểu null/list khi tạo template hay bảng rỗng. Có thể
đọc đầu vào `kind`/`entry_type` dạng string; exporter chuẩn hóa dictionary.
`version`/`year` cast an toàn về int32; ID arXiv không có suffix version, giữ
định dạng cũ như `hep-th/9901001`. Không chuyển ID bài sang tên thư mục có dấu `-`.

## Nhãn và pool — contract với Mẫn Nhi

- `manual_papers.parquet`: `owner`, `arxiv_id`, mỗi bài một dòng.
- `labels.parquet`: `arxiv_id`, `citation_key`, `groundtruth_arxiv_id`,
  `label_method`, `labeler`, `label_status`, `evidence`, `heuristic_version`.
- `label_method`: `manual` hoặc `automatic`.
- `label_status`: `matched`, `no_match`, `ambiguous`, `unreviewed`.
  Chỉ `matched` có ground truth; automatic không được để `unreviewed`.
- Từng entry trong pool phải có dòng labels, kể cả manual chưa xem. Missing row
  là lỗi; không tự đổi thành no-match. Ground truth phải thuộc references cùng bài.
- Mỗi owner >=5 manual paper, mỗi paper >=20 cặp đúng. Pool tự động chọn đúng
  `max(2, ceil((N-M)/10))` bài, không giảm mẫu số khi metadata thiếu.
- Dùng `make_pool` trước để module B biết chính xác danh sách bài cần xử lý.
  Không chạy heuristic lên manual pool. Không đổi seed/input/manual list sau
  khi đã gán nhãn mà không tạo lại artifact liên quan.

```python
from pathlib import Path
from arxiv_pipeline.matching.splitting import make_pool
pool = make_pool(Path('inventory.json'), Path('manual_papers.parquet'),
                 Path('pool.parquet'), seed=42)
# Module B dùng pool để tạo labels; runner tạo lại cùng pool một cách xác định.
```

`make_split(pool, labels, output_path, seed=42)` chọn theo bài: mỗi owner có
1 manual +1 automatic ở valid và tương tự ở test, còn lại vào train. Automatic
valid/test cần có >=1 matched entry. Kiểm tra cấu trúc/quota ngay khi split;
kiểm tra đủ entry và ground truth thuộc references trước train trong runner.
Gọi riêng make_split không thay thế kiểm tra labels với input của module B.

## Scores — contract với Khang

`scores.parquet`: `arxiv_id`, `citation_key`, `ref_arxiv_id`, `score` float64.
Mỗi entry của mọi bài trong split có score cho **toàn bộ** references cùng bài,
kể cả entry không có nhãn. Cặp không được trùng; không cho NaN/inf hoặc candidate
khác bài. Bài không có references không cần dòng scores cho các entry của bài đó.

Scores cao hơn xếp trước; hòa điểm dùng ref ID tăng dần. Model scorer không đọc
labels. Top5 không được chèn ground truth. Predictions xuất mọi entry trong
split, padding null khi ít hơn5 candidate. Trạng thái chưa xem/không khớp ở
labels riêng; ground truth predictions để null trong cả hai trường hợp.

## Chế độ artifacts — chạy được trước khi A–D hoàn tất

```json
{
  "mode": "artifacts",
  "inventory": "inventory.json",
  "manual_papers": "manual_papers.parquet",
  "labels": "labels.parquet",
  "scores": "scores-model.parquet",
  "output_dir": "runs/trong-unique-run-id",
  "owners": ["trong"],
  "seed": 42
}
```

Mọi path trong config tương đối với config hoặc tuyệt đối. `output_dir` phải
chưa tồn tại. Labels/scores phải khớp đúng pool theo seed và manual list.
Chế độ này kiểm tra/xếp hạng/export artifact có sẵn, không huấn luyện hay tính
MRR: `status=artifacts_complete`, `evaluation_status=not_run`.

## Chế độ full — sau khi có A–D

Đổi `mode` thành `full`, thêm `manual_dir`; bỏ `labels`, `scores` ngoài.
Dùng `inventory` đã chuẩn bị hoặc thêm `source_config` để gọi lại A và kiểm tra
corpus/ownership vẫn khớp inventory cấu hình. Runner import đúng các interface:

| Module | Hàm |
|---|---|
| A | `prepare_inputs(source_config, output_dir) -> inventory_path` nếu có source_config |
| B | `build_labels(inventory, pool, manual_dir, output_dir) -> labels_path` |
| C | `train(inventory, labels, split, model_dir) -> model_dir` |
| C | `score(inventory, split, model_dir, output_path, method) -> scores_path`; method baseline/model |
| D | `evaluate(inventory, labels, split, scores, predictions, report_dir) -> metrics_path` |
| D | `validate_dataset(dataset_dir, inventory, split, output_path) -> report_path` |

Mọi tham số đường dẫn là `Path`. Validator trả JSON có `valid: true` mới được
đánh dấu run hoàn thành. C fit preprocessing/model chỉ trên train, dùng valid
chọn cấu hình; test chỉ đánh giá sau khi chốt. D tính MRR@5 chỉ trên matched
entry, báo tổng và tách manual/automatic kèm số sample; Q rỗng không có điểm.
Runner kiểm tra đầy đủ artifact trước train nhưng không thay thế evaluator D.

## Output và bàn giao

```text
<output_dir>/
  run_config.json                  # status, input hash, seed, revision, elapsed
  pool.parquet
  split.parquet
  model/                           # full mode
  scores-{baseline,model}.parquet   # full mode
  predictions-{baseline,model}.parquet
  reports/                         # full mode: metrics và dataset validation
  export/<owner>/
    README.md                      # YAML 6 config
    data/<table>/part-00000.parquet # 6 bảng, tối đa250000 dòng/shard
```

Chỉ predictions của model vào bộ export. Năm bảng M1/M2 giữ toàn corpus owner,
không giới hạn label pool. References lọc theo `arxiv_id` của **bài trích dẫn**,
không lọc ref ID theo dải owner. Export stream từng batch và xuất bảng rỗng có
schema; tạo xong thư mục staging mới công bố dataset. File/thư mục đích đã có
được từ chối; chạy lại dùng run ID mới. Khi lỗi, xem `status=failed`,
`failed_stage`, `error` trong run_config; không coi thư mục còn lại là run đạt.
Full mode status complete chỉ xác nhận pipeline chạy, chưa xác nhận đã nộp HF.

Khi chia output, model/split dùng chung toàn nhóm; `owners` chỉ chọn phần xuất.
Trước nộp, từng người kiểm tra6 config, viewer/streaming, quyền public và tự nộp
link cá nhân. Module E không tự upload/nộp Moodle hay commit/push.

## Kiểm tra

```bash
python -m pip install pyarrow==23.0.1 pyyaml
PYTHONPATH=src python -m unittest discover -s tests/matching -v
PYTHONPATH=src python -m unittest discover -s tests -v
```

Fixture tests dùng dữ liệu nhân tạo có đánh dấu; full-mode test dùng adapter
ở biên để kiểm tra thứ tự điều phối. Đây không phải kết quả huấn luyện thật,
MRR thật hay benchmark Colab; phải kiểm tra lại với A–D và labels thật khi bàn giao.
