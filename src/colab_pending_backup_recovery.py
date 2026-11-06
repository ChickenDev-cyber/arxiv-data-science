"""Finish the existing Colab pending backup; keep all ZIPs and send no API requests.

Execute this in the SAME notebook/runtime namespace after the resume-31 error.
It does not modify the pinned checkout, stored Results, producer SHA or budgets.
"""
import json
from unittest.mock import patch


def recover_pending_backup(namespace, *, expected_measurement='resume-31'):
    session = namespace.get('session')
    worker = namespace.get('worker')
    if session is None or worker is None or session.backend is not worker:
        raise RuntimeError('Cần session và worker của cùng notebook/runtime đang giữ corpus.')
    if not session.journal_path.is_file():
        raise RuntimeError('Không thấy journal; dừng để kiểm tra, không tạo phiên crawl mới.')
    journal = json.loads(session.journal_path.read_text())
    expected_identity = (worker.owner, worker.run_id, worker.pipeline_commit)
    if tuple(journal.get(k) for k in ('owner', 'run_id', 'pipeline_commit')) != expected_identity:
        raise RuntimeError('Journal không khớp worker; giữ nguyên dữ liệu để kiểm tra.')
    pending = journal.get('pending')
    if (not isinstance(pending, dict) or pending.get('executed') is not True
            or journal.get('terminal') is True
            or pending.get('batch', {}).get('measurement_id') != expected_measurement
            or not isinstance(pending.get('after'), dict)
            or not isinstance(pending.get('report'), dict)):
        raise RuntimeError('Không có pending backup đã crawl xong đúng measurement đang chờ; dừng.')
    if not worker.batch_completed(pending['batch']):
        raise RuntimeError('Chưa có benchmark hoàn tất; không chạy lại task tự động.')

    def no_new_crawl(*args, **kwargs):
        raise RuntimeError('Ô phục hồi chỉ hoàn tất backup; đã chặn preflight và task mới.')

    def keep_all_archives(*args, **kwargs):
        print('Phục hồi backup: tạm giữ toàn bộ ZIP cũ; không dọn snapshot trong lần này.')

    print('Hoàn tất batch đang chờ:', expected_measurement)
    # Existing backup code still checks active identity, corpus revision, file contents
    # and source/destination SHA-256. Only retention is bypassed, and only in memory.
    with patch.object(worker, '_prune_snapshots', keep_all_archives), \
         patch.object(worker, 'preflight', no_new_crawl), \
         patch.object(worker, 'execute_batch', no_new_crawl):
        result = session.run(max_batches_this_call=1)

    saved = json.loads(session.journal_path.read_text())
    expected_after = pending['after']
    if (saved.get('pending') is not None or result['batches'] != expected_after['batches']
            or result['retry_passes'] != expected_after['retry_passes']
            or result['retry_before_attempt'] != expected_after['retry_before_attempt']):
        raise RuntimeError('Cần kiểm tra journal sau phục hồi; không crawl thêm.')
    print(json.dumps({k: result.get(k) for k in ('stop_reason', 'terminal', 'batches',
                     'retry_passes', 'retry_before_attempt', 'next_measurement', 'summary')},
                     ensure_ascii=False, indent=2))
    print('Backup đã hoàn tất. Giữ runtime này; chưa chạy Run all với code dọn ZIP cũ.')
    return result


if __name__ == '__main__':
    recover_pending_backup(globals())
