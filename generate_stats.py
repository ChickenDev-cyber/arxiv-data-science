import json
from pathlib import Path

def generate_statistics(run_id="baseline-001"):
    scope_path = Path(f"runs/{run_id}/scope.json")
    results_path = Path(f"runs/{run_id}/accepted_results.jsonl")
    
    # Đọc scope
    scope_data = {}
    if scope_path.exists():
        with open(scope_path, "r", encoding="utf-8") as f:
            scope_data = json.load(f)
    
    # Lấy danh sách bài từ scope.paper_tasks
    total_papers = len(scope_data.get("paper_tasks", []))
    
    # Khởi tạo output đúng cấu trúc đã chốt (schema 1.1)
    stats = {
        "schema_version": "1.1",
        "scope": {
            "run_id": run_id,
            "is_mock": scope_data.get("is_mock", True),
            "total_papers": total_papers
        },
        "summary": {
            "papers_discovered": total_papers,
            "metadata_complete_papers": 0,
            "papers_complete": 0,
            "total_download_bytes": 0,
            "total_retained_source_bytes": 0,
            "avg_references_per_paper": 0.0
        },
        "by_owner": {}
    }
    
    reference_counts = []
    
    # Đọc file accepted_results.jsonl theo schema 1.1
    if results_path.exists():
        with open(results_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                res = json.loads(line)
                
                # Các trường ở cấp ngoài
                owner = res.get("owner", "unknown")
                step = res.get("step")  # Loại task là step
                status = res.get("status")
                
                # Dữ liệu nghiệp vụ và số đo dung lượng nằm trong data và metrics
                data_payload = res.get("data", {})
                metrics_payload = res.get("metrics", {})
                
                # Thống kê theo owner cơ bản
                if owner not in stats["by_owner"]:
                    stats["by_owner"][owner] = {"success_count": 0}
                if status == "success":
                    stats["by_owner"][owner]["success_count"] += 1
                
                # Gom số liệu theo step
                if step == "metadata" and data_payload.get("versions_complete"):
                    stats["summary"]["metadata_complete_papers"] += 1
                
                if step == "sources":
                    # Lấy số liệu dung lượng từ metrics
                    stats["summary"]["total_download_bytes"] += metrics_payload.get("download_bytes", 0)
                    stats["summary"]["total_retained_source_bytes"] += metrics_payload.get("retained_source_bytes", 0)
                
                if step == "references":
                    # Lấy thông tin nghiệp vụ từ data
                    ref_cnt = data_payload.get("s2_reference_count", 0)
                    reference_counts.append(ref_cnt)

    if total_papers > 0:
        stats["summary"]["papers_complete"] = stats["summary"]["metadata_complete_papers"]
        if reference_counts:
            stats["summary"]["avg_references_per_paper"] = sum(reference_counts) / len(reference_counts)

    # Lưu kết quả ra thư mục statistics/
    output_dir = Path("statistics")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{run_id}.json"
    
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
        
    print(f"Đã tạo thành công file thống kê schema 1.1 tại: {output_file}")

if __name__ == "__main__":
    generate_statistics()