"""Real byte-stream fixtures shared by T3 transport and service tests."""

from http.client import HTTPResponse
import io
import zipfile


def make_zip(members: dict[str, bytes], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def make_docx(*, document: bytes | None = None, extra_members=None,
              compression=zipfile.ZIP_DEFLATED) -> bytes:
    members = {
        "[Content_Types].xml": (
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Override PartName="/word/document.xml" '
            b'ContentType="application/vnd.openxmlformats-officedocument.'
            b'wordprocessingml.document.main+xml"/></Types>'
        ),
        "_rels/.rels": (
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" Target="word/document.xml" '
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            b'officeDocument"/></Relationships>'
        ),
        "word/document.xml": document if document is not None else (
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b'<w:body><w:p><w:r><w:t>An example document</w:t></w:r></w:p></w:body></w:document>'
        ),
    }
    members.update(extra_members or {})
    return make_zip(members, compression=compression)


def truncated_chunked_response():
    """One complete 64 KiB HTTP chunk followed by a truncated chunk."""
    class ByteSocket:
        def makefile(self, *args, **kwargs):
            return io.BytesIO(
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
                b"10000\r\n" + b"x" * 65536 + b"\r\n10\r\nabc"
            )

    response = HTTPResponse(ByteSocket())
    response.begin()
    return response
