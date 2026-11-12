"""Ghép các bước chuẩn hóa một đoạn văn xuôi LaTeX."""

from typing import Callable

from .cleaner import clean_text, normalize_math
from .tokenizer import tokenize_sentences


def process_prose(
    text: str,
    custom_tokenizer: Callable[[str], list[str]] | None = None,
) -> list[str]:
    """Nhận prose đã tách cấu trúc, trả danh sách câu theo thứ tự gốc.

    Hierarchy chịu trách nhiệm tách block formula và figure/table trước khi gọi.
    Citation key đã được rewrite ở bước trước; hàm này giữ nguyên các key đó.
    """
    # Bỏ định dạng trước để lệnh bọc chữ không che mất ranh giới câu bên trong.
    cleaned = clean_text(text)
    # Thống nhất math trước khi tách câu để output dùng cùng một biểu diễn.
    normalized = normalize_math(cleaned)
    # Giữ điểm tùy biến đang có của hierarchy; mặc định dùng tokenizer Module 2.
    tokenizer = custom_tokenizer if custom_tokenizer is not None else tokenize_sentences
    return tokenizer(normalized)
