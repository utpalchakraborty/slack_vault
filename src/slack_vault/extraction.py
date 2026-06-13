"""Deterministic extraction of source documents into citable evidence."""

from __future__ import annotations

import logging
import posixpath
import re
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from xml.etree import ElementTree

from pypdf import PdfReader

from slack_vault.archive import ArchivedSourceRef

logger = logging.getLogger(__name__)

DOCX_MIME_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
SPREADSHEET_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELATIONSHIP_NAMESPACE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
PACKAGE_RELATIONSHIP_NAMESPACE = (
    "http://schemas.openxmlformats.org/package/2006/relationships"
)

WORD_NS = {"w": WORD_NAMESPACE}
SHEET_NS = {
    "main": SPREADSHEET_NAMESPACE,
    "r": RELATIONSHIP_NAMESPACE,
    "rel": PACKAGE_RELATIONSHIP_NAMESPACE,
}

MARKDOWN_EXTENSIONS = {".md", ".markdown"}
TEXT_EXTENSIONS = {".txt", ".text"}
PDF_EXTENSIONS = {".pdf"}
DOCX_EXTENSIONS = {".docx"}
XLSX_EXTENSIONS = {".xlsx"}

MARKDOWN_MIME_TYPES = {"text/markdown", "text/x-markdown"}
TEXT_MIME_TYPES = {"text/plain"}

HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
CELL_REFERENCE_PATTERN = re.compile(r"^([A-Z]+)([0-9]+)$")


class ExtractionStatus(StrEnum):
    """Document extraction status values stored in source records."""

    COMPLETED = "completed"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class EvidenceLocationKind(StrEnum):
    """Location anchor type for an extracted evidence block."""

    FILE = "file"
    HEADING = "heading"
    PAGE = "page"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    SHEET = "sheet"


@dataclass(frozen=True)
class EvidenceLocation:
    """Source-grounded location for an evidence block."""

    kind: EvidenceLocationKind
    file_name: str
    page_number: int | None = None
    heading: str | None = None
    paragraph_index: int | None = None
    table_index: int | None = None
    sheet_name: str | None = None
    cell_range: str | None = None

    def label(self) -> str:
        """Return a human-readable location label."""

        if self.kind is EvidenceLocationKind.PAGE and self.page_number is not None:
            return f"{self.file_name}, page {self.page_number}"
        if self.kind is EvidenceLocationKind.HEADING and self.heading is not None:
            return f'{self.file_name}, heading "{self.heading}"'
        if (
            self.kind is EvidenceLocationKind.PARAGRAPH
            and self.paragraph_index is not None
        ):
            label = f"{self.file_name}, paragraph {self.paragraph_index}"
            if self.heading is not None:
                label += f' under "{self.heading}"'
            return label
        if self.kind is EvidenceLocationKind.TABLE and self.table_index is not None:
            label = f"{self.file_name}, table {self.table_index}"
            if self.heading is not None:
                label += f' under "{self.heading}"'
            return label
        if self.kind is EvidenceLocationKind.SHEET and self.sheet_name is not None:
            label = f'{self.file_name}, sheet "{self.sheet_name}"'
            if self.cell_range is not None:
                label += f" {self.cell_range}"
            return label
        return self.file_name


@dataclass(frozen=True)
class EvidenceBlock:
    """A normalized block of extracted evidence."""

    sequence: int
    text: str
    location: EvidenceLocation


@dataclass(frozen=True)
class ExtractionResult:
    """Result of deterministic extraction."""

    status: ExtractionStatus
    extractor_name: str
    evidence: tuple[EvidenceBlock, ...] = ()
    error_message: str | None = None

    @classmethod
    def completed(
        cls,
        *,
        extractor_name: str,
        evidence: tuple[EvidenceBlock, ...],
    ) -> ExtractionResult:
        """Create a successful extraction result."""

        return cls(
            status=ExtractionStatus.COMPLETED,
            extractor_name=extractor_name,
            evidence=evidence,
        )

    @classmethod
    def failed(cls, *, extractor_name: str, error_message: str) -> ExtractionResult:
        """Create a failed extraction result."""

        return cls(
            status=ExtractionStatus.FAILED,
            extractor_name=extractor_name,
            error_message=error_message,
        )

    @classmethod
    def unsupported(cls, ref: ArchivedSourceRef) -> ExtractionResult:
        """Create a result for an unsupported source type."""

        return cls(
            status=ExtractionStatus.UNSUPPORTED,
            extractor_name="none",
            error_message=f"Unsupported MIME type: {ref.mime_type}",
        )


class DocumentExtractor(Protocol):
    """Interface for deterministic document extractors."""

    name: str

    def supports(self, ref: ArchivedSourceRef, file_path: Path) -> bool:
        """Return whether this extractor can handle the archived source."""

    def extract(self, ref: ArchivedSourceRef, file_path: Path) -> ExtractionResult:
        """Extract source-grounded evidence from a document."""


class MarkdownExtractor:
    """Extract Markdown evidence by heading section."""

    name = "markdown"

    def supports(self, ref: ArchivedSourceRef, file_path: Path) -> bool:
        return _extension(ref, file_path) in MARKDOWN_EXTENSIONS or (
            ref.mime_type in MARKDOWN_MIME_TYPES
        )

    def extract(self, ref: ArchivedSourceRef, file_path: Path) -> ExtractionResult:
        text = file_path.read_text(encoding="utf-8", errors="replace")
        sections = _markdown_sections(text)
        evidence = tuple(
            _evidence_block(
                sequence=index,
                text=section_text,
                location=EvidenceLocation(
                    kind=EvidenceLocationKind.HEADING
                    if heading is not None
                    else EvidenceLocationKind.FILE,
                    file_name=ref.original_filename,
                    heading=heading,
                ),
            )
            for index, (heading, section_text) in enumerate(sections, start=1)
        )
        return ExtractionResult.completed(
            extractor_name=self.name,
            evidence=evidence,
        )


class PlainTextExtractor:
    """Extract a plain text file as a single file-level evidence block."""

    name = "plain_text"

    def supports(self, ref: ArchivedSourceRef, file_path: Path) -> bool:
        extension = _extension(ref, file_path)
        return extension in TEXT_EXTENSIONS or ref.mime_type in TEXT_MIME_TYPES

    def extract(self, ref: ArchivedSourceRef, file_path: Path) -> ExtractionResult:
        text = file_path.read_text(encoding="utf-8", errors="replace").strip()
        evidence: tuple[EvidenceBlock, ...] = ()
        if text:
            evidence = (
                _evidence_block(
                    sequence=1,
                    text=text,
                    location=EvidenceLocation(
                        kind=EvidenceLocationKind.FILE,
                        file_name=ref.original_filename,
                    ),
                ),
            )
        return ExtractionResult.completed(
            extractor_name=self.name,
            evidence=evidence,
        )


class PdfExtractor:
    """Extract PDF text with page-level anchors."""

    name = "pdf"

    def supports(self, ref: ArchivedSourceRef, file_path: Path) -> bool:
        return _extension(ref, file_path) in PDF_EXTENSIONS or (
            ref.mime_type == "application/pdf"
        )

    def extract(self, ref: ArchivedSourceRef, file_path: Path) -> ExtractionResult:
        reader = PdfReader(file_path)
        blocks: list[EvidenceBlock] = []
        for page_index, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            blocks.append(
                _evidence_block(
                    sequence=len(blocks) + 1,
                    text=text,
                    location=EvidenceLocation(
                        kind=EvidenceLocationKind.PAGE,
                        file_name=ref.original_filename,
                        page_number=page_index,
                    ),
                )
            )
        return ExtractionResult.completed(
            extractor_name=self.name,
            evidence=tuple(blocks),
        )


class DocxExtractor:
    """Extract DOCX paragraphs and tables with paragraph/table anchors."""

    name = "docx"

    def supports(self, ref: ArchivedSourceRef, file_path: Path) -> bool:
        return _extension(ref, file_path) in DOCX_EXTENSIONS or (
            ref.mime_type == DOCX_MIME_TYPE
        )

    def extract(self, ref: ArchivedSourceRef, file_path: Path) -> ExtractionResult:
        with zipfile.ZipFile(file_path) as docx:
            document_xml = docx.read("word/document.xml")

        root = ElementTree.fromstring(document_xml)
        body = root.find("w:body", WORD_NS)
        if body is None:
            raise ValueError("DOCX document body is missing")

        blocks: list[EvidenceBlock] = []
        current_heading: str | None = None
        paragraph_index = 0
        table_index = 0

        for child in body:
            if child.tag == _word_tag("p"):
                text = _word_paragraph_text(child).strip()
                if not text:
                    continue

                paragraph_index += 1
                style = _word_paragraph_style(child)
                is_heading = style is not None and style.lower().startswith("heading")
                if is_heading:
                    current_heading = text
                blocks.append(
                    _evidence_block(
                        sequence=len(blocks) + 1,
                        text=text,
                        location=EvidenceLocation(
                            kind=EvidenceLocationKind.HEADING
                            if is_heading
                            else EvidenceLocationKind.PARAGRAPH,
                            file_name=ref.original_filename,
                            heading=text if is_heading else current_heading,
                            paragraph_index=paragraph_index,
                        ),
                    )
                )
            elif child.tag == _word_tag("tbl"):
                table_text = _word_table_text(child).strip()
                if not table_text:
                    continue

                table_index += 1
                blocks.append(
                    _evidence_block(
                        sequence=len(blocks) + 1,
                        text=table_text,
                        location=EvidenceLocation(
                            kind=EvidenceLocationKind.TABLE,
                            file_name=ref.original_filename,
                            heading=current_heading,
                            table_index=table_index,
                        ),
                    )
                )

        return ExtractionResult.completed(
            extractor_name=self.name,
            evidence=tuple(blocks),
        )


class XlsxExtractor:
    """Extract XLSX workbook values with sheet and cell-range anchors."""

    name = "xlsx"

    def supports(self, ref: ArchivedSourceRef, file_path: Path) -> bool:
        return _extension(ref, file_path) in XLSX_EXTENSIONS or (
            ref.mime_type == XLSX_MIME_TYPE
        )

    def extract(self, ref: ArchivedSourceRef, file_path: Path) -> ExtractionResult:
        with zipfile.ZipFile(file_path) as workbook:
            shared_strings = _xlsx_shared_strings(workbook)
            sheets = _xlsx_sheet_paths(workbook)
            blocks = [
                _xlsx_sheet_block(
                    workbook=workbook,
                    shared_strings=shared_strings,
                    sheet_name=sheet_name,
                    sheet_path=sheet_path,
                    file_name=ref.original_filename,
                    sequence=index,
                )
                for index, (sheet_name, sheet_path) in enumerate(sheets, start=1)
            ]

        return ExtractionResult.completed(
            extractor_name=self.name,
            evidence=tuple(block for block in blocks if block is not None),
        )


def default_extractors() -> tuple[DocumentExtractor, ...]:
    """Return the default deterministic extractor chain."""

    return (
        MarkdownExtractor(),
        PlainTextExtractor(),
        PdfExtractor(),
        DocxExtractor(),
        XlsxExtractor(),
    )


def extract_document(
    ref: ArchivedSourceRef,
    file_path: Path,
    *,
    extractors: tuple[DocumentExtractor, ...] | None = None,
) -> ExtractionResult:
    """Extract source-grounded evidence using the first matching extractor."""

    for extractor in extractors or default_extractors():
        if not extractor.supports(ref, file_path):
            continue
        logger.info(
            "Extractor selected extractor=%s filename=%s mime_type=%s",
            extractor.name,
            ref.original_filename,
            ref.mime_type,
        )
        try:
            result = extractor.extract(ref, file_path)
            logger.info(
                "Extractor completed extractor=%s evidence_blocks=%s",
                extractor.name,
                len(result.evidence),
            )
            return result
        except Exception as exc:
            logger.exception(
                "Extractor failed extractor=%s filename=%s",
                extractor.name,
                ref.original_filename,
            )
            return ExtractionResult.failed(
                extractor_name=extractor.name,
                error_message=str(exc),
            )
    logger.warning(
        "No extractor supports source filename=%s mime_type=%s",
        ref.original_filename,
        ref.mime_type,
    )
    return ExtractionResult.unsupported(ref)


def _extension(ref: ArchivedSourceRef, file_path: Path) -> str:
    original_extension = Path(ref.original_filename).suffix.lower()
    return original_extension or file_path.suffix.lower()


def _evidence_block(
    *,
    sequence: int,
    text: str,
    location: EvidenceLocation,
) -> EvidenceBlock:
    return EvidenceBlock(sequence=sequence, text=text.strip(), location=location)


def _markdown_sections(text: str) -> tuple[tuple[str | None, str], ...]:
    sections: list[tuple[str | None, str]] = []
    current_heading: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        section_text = "\n".join(current_lines).strip()
        if section_text:
            sections.append((current_heading, section_text))

    for line in text.splitlines():
        heading_match = HEADING_PATTERN.match(line)
        if heading_match is not None:
            flush()
            current_heading = heading_match.group(2).strip()
            current_lines = [line]
            continue
        current_lines.append(line)

    flush()
    return tuple(sections)


def _word_tag(local_name: str) -> str:
    return f"{{{WORD_NAMESPACE}}}{local_name}"


def _word_paragraph_style(paragraph: ElementTree.Element) -> str | None:
    style = paragraph.find("w:pPr/w:pStyle", WORD_NS)
    if style is None:
        return None
    value = style.attrib.get(_word_tag("val"))
    return value.strip() if value is not None else None


def _word_paragraph_text(paragraph: ElementTree.Element) -> str:
    return "".join(node.text or "" for node in paragraph.findall(".//w:t", WORD_NS))


def _word_table_text(table: ElementTree.Element) -> str:
    rows: list[str] = []
    for row in table.findall("w:tr", WORD_NS):
        cells = [
            " ".join(
                paragraph_text
                for paragraph_text in (
                    _word_paragraph_text(paragraph).strip()
                    for paragraph in cell.findall("w:p", WORD_NS)
                )
                if paragraph_text
            )
            for cell in row.findall("w:tc", WORD_NS)
        ]
        row_text = " | ".join(cell_text for cell_text in cells if cell_text)
        if row_text:
            rows.append(row_text)
    return "\n".join(rows)


def _xlsx_shared_strings(workbook: zipfile.ZipFile) -> tuple[str, ...]:
    try:
        shared_strings_xml = workbook.read("xl/sharedStrings.xml")
    except KeyError:
        return ()

    root = ElementTree.fromstring(shared_strings_xml)
    strings: list[str] = []
    for item in root.findall("main:si", SHEET_NS):
        strings.append(
            "".join(node.text or "" for node in item.findall(".//main:t", SHEET_NS))
        )
    return tuple(strings)


def _xlsx_sheet_paths(workbook: zipfile.ZipFile) -> tuple[tuple[str, str], ...]:
    workbook_root = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
    relationships_root = ElementTree.fromstring(
        workbook.read("xl/_rels/workbook.xml.rels")
    )
    targets = {
        relationship.attrib["Id"]: relationship.attrib["Target"]
        for relationship in relationships_root.findall("rel:Relationship", SHEET_NS)
    }
    sheets = workbook_root.find("main:sheets", SHEET_NS)
    if sheets is None:
        return ()

    sheet_paths: list[tuple[str, str]] = []
    for sheet in sheets.findall("main:sheet", SHEET_NS):
        name = sheet.attrib.get("name", "Sheet")
        relationship_id = sheet.attrib.get(f"{{{RELATIONSHIP_NAMESPACE}}}id")
        if relationship_id is None or relationship_id not in targets:
            continue
        sheet_paths.append((name, _xlsx_target_path(targets[relationship_id])))
    return tuple(sheet_paths)


def _xlsx_target_path(target: str) -> str:
    if target.startswith("/"):
        return target.removeprefix("/")
    return posixpath.normpath(posixpath.join("xl", target))


def _xlsx_sheet_block(
    *,
    workbook: zipfile.ZipFile,
    shared_strings: tuple[str, ...],
    sheet_name: str,
    sheet_path: str,
    file_name: str,
    sequence: int,
) -> EvidenceBlock | None:
    sheet_root = ElementTree.fromstring(workbook.read(sheet_path))
    rows = sheet_root.findall(".//main:sheetData/main:row", SHEET_NS)
    rendered_rows: list[str] = []
    cell_references: list[str] = []

    for row in rows:
        rendered_cells: list[str] = []
        for cell in row.findall("main:c", SHEET_NS):
            cell_reference = cell.attrib.get("r", "")
            value = _xlsx_cell_value(cell, shared_strings)
            if not cell_reference or not value:
                continue
            cell_references.append(cell_reference)
            rendered_cells.append(f"{cell_reference}: {value}")
        if rendered_cells:
            rendered_rows.append(" | ".join(rendered_cells))

    if not rendered_rows:
        return None

    text = "\n".join([f"Sheet: {sheet_name}", *rendered_rows])
    return _evidence_block(
        sequence=sequence,
        text=text,
        location=EvidenceLocation(
            kind=EvidenceLocationKind.SHEET,
            file_name=file_name,
            sheet_name=sheet_name,
            cell_range=_xlsx_cell_range(cell_references),
        ),
    )


def _xlsx_cell_value(
    cell: ElementTree.Element,
    shared_strings: tuple[str, ...],
) -> str:
    cell_type = cell.attrib.get("t")
    formula = cell.find("main:f", SHEET_NS)
    value_node = cell.find("main:v", SHEET_NS)
    formula_text = (formula.text or "").strip() if formula is not None else None

    if cell_type == "s" and value_node is not None and value_node.text is not None:
        value = _shared_string_value(value_node.text, shared_strings)
    elif cell_type == "inlineStr":
        value = "".join(
            node.text or "" for node in cell.findall(".//main:t", SHEET_NS)
        ).strip()
    elif value_node is not None and value_node.text is not None:
        value = value_node.text.strip()
    else:
        value = ""

    if formula_text is None:
        return value
    if not value:
        return f"={formula_text}"
    return f"={formula_text} -> {value}"


def _shared_string_value(index_text: str, shared_strings: tuple[str, ...]) -> str:
    try:
        return shared_strings[int(index_text)].strip()
    except (IndexError, ValueError):
        return index_text.strip()


def _xlsx_cell_range(cell_references: list[str]) -> str | None:
    parsed: list[tuple[int, int]] = []
    for cell_reference in cell_references:
        parsed_cell = _parse_cell_reference(cell_reference)
        if parsed_cell is not None:
            parsed.append(parsed_cell)

    if not parsed:
        return None

    min_column = min(column for column, _row in parsed)
    max_column = max(column for column, _row in parsed)
    min_row = min(row for _column, row in parsed)
    max_row = max(row for _column, row in parsed)
    return (
        f"{_column_letters(min_column)}{min_row}:{_column_letters(max_column)}{max_row}"
    )


def _parse_cell_reference(cell_reference: str) -> tuple[int, int] | None:
    match = CELL_REFERENCE_PATTERN.match(cell_reference)
    if match is None:
        return None
    column_letters, row_text = match.groups()
    column = 0
    for letter in column_letters:
        column = column * 26 + (ord(letter) - ord("A") + 1)
    return column, int(row_text)


def _column_letters(column: int) -> str:
    letters = ""
    while column:
        column, remainder = divmod(column - 1, 26)
        letters = f"{chr(ord('A') + remainder)}{letters}"
    return letters
