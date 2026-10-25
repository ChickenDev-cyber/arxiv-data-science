"""Per-paper terminal counts; the published T6 statistics remain a separate task."""
from ..validation.corpus import paper_issues


OWNER_NAMES = {
    'khang': 'Khang', 'trong': 'Trọng', 'hoang': 'Hoàng',
    'man-nhi': 'Mẫn Nhi', 'huynh-nhi': 'Huỳnh Nhi',
}


def paper_outcome(task, results):
    """Consume validated results, preferring accepted data over failed retries."""
    owned = [r for r in results if r['owner'] == task['owner']
             and r['arxiv_id'] == task['arxiv_id'] and r['step'] != 'discovery']
    accepted = {(r['owner'], r['step'], r['arxiv_id'], r['version']): r for r in owned
                if r['status'] in ('success', 'partial', 'empty', 'unsupported')}
    if not paper_issues(task, accepted):
        return 'success'
    if accepted:
        return 'partial'
    return 'failed' if owned else 'pending'


def summary_line(owner, total, counts):
    name = 'toàn bộ mẫu' if owner is None else OWNER_NAMES.get(owner, owner)
    line = (f"Tổng kết {name} | {total} bài | thành công: {counts.get('success', 0)}"
            f" | thiếu: {counts.get('partial', 0)} | thất bại: {counts.get('failed', 0)}")
    if not total:
        line += ' | chưa có bài được chọn; kiểm tra discovery'
    return line
