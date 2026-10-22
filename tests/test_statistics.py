import json
import os
from pathlib import Path
from generate_stats import generate_statistics

def test_generate_statistics_schema():
    """Kiểm tra logic đọc schema 1.1 của thư viện thống kê"""
    test_run_id = "test-mock-001"
    
    # 1. Tạo thư mục giả lập
    run_dir = Path(f"runs/{test_run_id}")
    run_dir.mkdir(parents=True, exist_ok=True)
    
    # 2. Tạo file scope.json giả
    scope_data = {"is_mock": True, "paper_tasks": ["2310.12345v1", "2310.12345v2"]}
    with open(run_dir / "scope.json", "w", encoding="utf-8") as f:
        json.dump(scope_data, f)
        
    # 3. Tạo file accepted_results.jsonl giả (chuẩn schema 1.1)
    mock_results = [
        {"owner": "Hoang", "step": "metadata", "status": "success", "data": {"versions_complete": True}},
        {"owner": "Trong", "step": "sources", "status": "success", "metrics": {"download_bytes": 1000, "retained_source_bytes": 500}},
        {"owner": "Hoang", "step": "references", "status": "success", "data": {"s2_reference_count": 10}}
    ]
    with open(run_dir / "accepted_results.jsonl", "w", encoding="utf-8") as f:
        for res in mock_results:
            f.write(json.dumps(res) + "\n")
            
    # 4. Chạy hàm thống kê
    generate_statistics(run_id=test_run_id)
    
    # 5. Kiểm tra file output
    output_file = Path(f"statistics/{test_run_id}.json")
    assert output_file.exists(), "File thống kê chưa được tạo"
    
    with open(output_file, "r", encoding="utf-8") as f:
        stats = json.load(f)
        
    # Kiểm tra các số liệu xem có tính đúng không
    assert stats["schema_version"] == "1.1"
    assert stats["scope"]["total_papers"] == 2
    assert stats["summary"]["metadata_complete_papers"] == 1
    assert stats["summary"]["total_download_bytes"] == 1000
    assert stats["summary"]["total_retained_source_bytes"] == 500
    assert stats["summary"]["avg_references_per_paper"] == 10.0
    assert stats["by_owner"]["Hoang"]["success_count"] == 2
    assert stats["by_owner"]["Trong"]["success_count"] == 1

    print("✅ Đã pass toàn bộ Unit Test cho phần Thống kê Schema 1.1!")