from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path

from slack_vault.archive import ArchivedSourceRef
from slack_vault.config import ArchiveProviderKind
from slack_vault.extraction import ExtractionStatus, extract_document


def test_markdown_extractor_uses_heading_sections(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    source.write_text(
        "# Overview\n\nPhase 2 extracts evidence.\n\n## Details\n\nAnchors matter.",
        encoding="utf-8",
    )
    ref = _archived_source_ref(source, mime_type="text/markdown")

    result = extract_document(ref, source)

    assert result.status is ExtractionStatus.COMPLETED
    assert result.extractor_name == "markdown"
    assert len(result.evidence) == 2
    assert result.evidence[0].location.label() == 'source.md, heading "Overview"'
    assert "Phase 2 extracts evidence." in result.evidence[0].text
    assert result.evidence[1].location.label() == 'source.md, heading "Details"'


def test_plain_text_extractor_creates_file_level_evidence(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("plain source evidence", encoding="utf-8")
    ref = _archived_source_ref(source, mime_type="text/plain")

    result = extract_document(ref, source)

    assert result.status is ExtractionStatus.COMPLETED
    assert result.extractor_name == "plain_text"
    assert len(result.evidence) == 1
    assert result.evidence[0].location.label() == "source.txt"
    assert result.evidence[0].text == "plain source evidence"


def test_pdf_extractor_uses_page_anchors(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    _write_pdf(source, "Phase 2 PDF evidence")
    ref = _archived_source_ref(source, mime_type="application/pdf")

    result = extract_document(ref, source)

    assert result.status is ExtractionStatus.COMPLETED
    assert result.extractor_name == "pdf"
    assert len(result.evidence) == 1
    assert result.evidence[0].location.label() == "source.pdf, page 1"
    assert "Phase 2 PDF evidence" in result.evidence[0].text


def test_docx_extractor_uses_paragraph_and_table_anchors(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    _write_docx(source)
    ref = _archived_source_ref(
        source,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    result = extract_document(ref, source)

    assert result.status is ExtractionStatus.COMPLETED
    assert result.extractor_name == "docx"
    assert len(result.evidence) == 3
    assert result.evidence[0].location.label() == 'source.docx, heading "Launch Plan"'
    assert (
        result.evidence[1].location.label()
        == 'source.docx, paragraph 2 under "Launch Plan"'
    )
    assert result.evidence[1].text == "Ship deterministic extraction."
    assert (
        result.evidence[2].location.label()
        == 'source.docx, table 1 under "Launch Plan"'
    )
    assert "Owner | Status" in result.evidence[2].text


def test_xlsx_extractor_uses_sheet_and_cell_range_anchors(tmp_path: Path) -> None:
    source = tmp_path / "source.xlsx"
    _write_xlsx(source)
    ref = _archived_source_ref(
        source,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    result = extract_document(ref, source)

    assert result.status is ExtractionStatus.COMPLETED
    assert result.extractor_name == "xlsx"
    assert len(result.evidence) == 1
    assert result.evidence[0].location.label() == 'source.xlsx, sheet "Roadmap" A1:B2'
    assert "A1: Item | B1: Status" in result.evidence[0].text
    assert "A2: Extractors | B2: Done" in result.evidence[0].text


def test_unsupported_file_returns_unsupported_result(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"\x00\x01")
    ref = _archived_source_ref(source, mime_type="application/octet-stream")

    result = extract_document(ref, source)

    assert result.status is ExtractionStatus.UNSUPPORTED
    assert result.extractor_name == "none"
    assert result.evidence == ()
    assert result.error_message == "Unsupported MIME type: application/octet-stream"


def test_pdf_failure_returns_failed_result(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_text("not really a pdf", encoding="utf-8")
    ref = _archived_source_ref(source, mime_type="application/pdf")

    result = extract_document(ref, source)

    assert result.status is ExtractionStatus.FAILED
    assert result.extractor_name == "pdf"
    assert result.evidence == ()
    assert result.error_message is not None


def _archived_source_ref(source: Path, *, mime_type: str) -> ArchivedSourceRef:
    return ArchivedSourceRef(
        archive_provider=ArchiveProviderKind.LOCAL,
        archive_id="sources/2026/06/abcdef1234567890",
        uri=str(source),
        content_hash="abcdef1234567890",
        original_filename=source.name,
        mime_type=mime_type,
        size_bytes=source.stat().st_size,
        created_at=datetime(2026, 6, 13, 12, 0, tzinfo=UTC),
        ingestion_method="local_file",
        original_path=str(source),
    )


def _write_pdf(path: Path, text: str) -> None:
    escaped_text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT\n/F1 12 Tf\n72 720 Td\n({escaped_text}) Tj\nET\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R "
            b"/Resources << /Font << /F1 4 0 R >> >> "
            b"/MediaBox [0 0 612 792] /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        b"" + stream + b"endstream",
    ]

    content = b"%PDF-1.4\n"
    offsets: list[int] = []
    for object_index, pdf_object in enumerate(objects, start=1):
        offsets.append(len(content))
        content += f"{object_index} 0 obj\n".encode("ascii")
        content += pdf_object + b"\nendobj\n"

    xref_offset = len(content)
    content += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    content += b"0000000000 65535 f \n"
    for offset in offsets:
        content += f"{offset:010d} 00000 n \n".encode("ascii")
    content += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    path.write_bytes(content)


def _write_docx(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Launch Plan</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>Ship deterministic extraction.</w:t></w:r></w:p>
    <w:tbl>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Owner</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Status</w:t></w:r></w:p></w:tc>
      </w:tr>
      <w:tr>
        <w:tc><w:p><w:r><w:t>Platform</w:t></w:r></w:p></w:tc>
        <w:tc><w:p><w:r><w:t>Ready</w:t></w:r></w:p></w:tc>
      </w:tr>
    </w:tbl>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(path, "w") as docx:
        docx.writestr("word/document.xml", document_xml)


def _write_xlsx(path: Path) -> None:
    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook
  xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Roadmap" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"""
    relationships_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships
  xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship
    Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
    Target="worksheets/sheet1.xml"/>
</Relationships>
"""
    shared_strings_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <si><t>Item</t></si>
  <si><t>Status</t></si>
  <si><t>Extractors</t></si>
  <si><t>Done</t></si>
</sst>
"""
    sheet_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData>
    <row r="1">
      <c r="A1" t="s"><v>0</v></c>
      <c r="B1" t="s"><v>1</v></c>
    </row>
    <row r="2">
      <c r="A2" t="s"><v>2</v></c>
      <c r="B2" t="s"><v>3</v></c>
    </row>
  </sheetData>
</worksheet>
"""
    with zipfile.ZipFile(path, "w") as workbook:
        workbook.writestr("xl/workbook.xml", workbook_xml)
        workbook.writestr("xl/_rels/workbook.xml.rels", relationships_xml)
        workbook.writestr("xl/sharedStrings.xml", shared_strings_xml)
        workbook.writestr("xl/worksheets/sheet1.xml", sheet_xml)
