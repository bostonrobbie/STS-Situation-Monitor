"""CSV and PDF export utilities for observations, reports, and search results."""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from sqlalchemy.orm import Session

from sts_monitor.models import ClaimORM, ObservationORM, ReportORM


def export_observations_csv(session: Session, investigation_id: str, filters: dict[str, Any] | None = None) -> str:
    """Export observations for an investigation as CSV."""
    query = session.query(ObservationORM).filter(ObservationORM.investigation_id == investigation_id)

    if filters:
        if filters.get("source"):
            query = query.filter(ObservationORM.source.ilike(f"%{filters['source']}%"))
        if filters.get("min_reliability"):
            query = query.filter(ObservationORM.reliability_hint >= filters["min_reliability"])

    query = query.order_by(ObservationORM.captured_at.desc())
    rows = query.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "source", "claim", "url", "captured_at", "reliability_hint", "latitude", "longitude", "connector_type"])
    for row in rows:
        writer.writerow([
            row.id, row.source, row.claim, row.url,
            row.captured_at.isoformat() if row.captured_at else "",
            row.reliability_hint, row.latitude, row.longitude, row.connector_type,
        ])
    return output.getvalue()


def export_claims_csv(session: Session, investigation_id: str) -> str:
    """Export claims for an investigation as CSV."""
    rows = session.query(ClaimORM).filter(ClaimORM.investigation_id == investigation_id).order_by(ClaimORM.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "claim_text", "stance", "confidence", "created_at"])
    for row in rows:
        writer.writerow([row.id, row.claim_text, row.stance, row.confidence, row.created_at.isoformat() if row.created_at else ""])
    return output.getvalue()


def export_report_markdown(session: Session, investigation_id: str) -> str:
    """Export the latest report as Markdown."""
    report = (
        session.query(ReportORM)
        .filter(ReportORM.investigation_id == investigation_id)
        .order_by(ReportORM.generated_at.desc())
        .first()
    )
    if not report:
        return ""

    lines = [
        f"# Situation Report: {investigation_id}",
        f"Generated: {report.generated_at.isoformat() if report.generated_at else 'N/A'}",
        f"Confidence: {report.confidence:.1%}",
        "",
        "## Summary",
        report.summary,
        "",
    ]

    # Include accepted observations summary
    try:
        accepted = json.loads(report.accepted_json)
        lines.append(f"## Accepted Observations ({len(accepted)})")
        for obs in accepted[:50]:
            claim = obs.get("claim", "")[:200]
            source = obs.get("source", "unknown")
            lines.append(f"- **[{source}]** {claim}")
    except (json.JSONDecodeError, TypeError):
        pass

    return "\n".join(lines)


def export_report_pdf_bytes(session: Session, investigation_id: str) -> bytes:
    """Export the latest report as a minimal PDF.

    Uses a pure-Python PDF builder (no external dependencies) to generate a
    basic but readable PDF document.
    """
    markdown = export_report_markdown(session, investigation_id)
    if not markdown:
        return b""
    return _build_simple_pdf(markdown)


def _build_simple_pdf(text: str) -> bytes:
    """Build a minimal valid PDF from plain text.

    This is a self-contained PDF generator that requires no external libraries.
    It produces a clean, readable document with basic formatting.
    """
    lines = text.split("\n")
    page_lines: list[list[str]] = []
    current_page: list[str] = []
    max_lines_per_page = 55

    for line in lines:
        current_page.append(line)
        if len(current_page) >= max_lines_per_page:
            page_lines.append(current_page)
            current_page = []
    if current_page:
        page_lines.append(current_page)
    if not page_lines:
        page_lines = [[""]]

    objects: list[bytes] = []
    offsets: list[int] = []

    def add_obj(content: bytes) -> int:
        obj_num = len(objects) + 1
        objects.append(content)
        return obj_num

    # Object 1: Catalog
    add_obj(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")

    # Object 2: Pages (placeholder — we'll replace after building page objects)
    pages_obj_idx = add_obj(b"")  # placeholder

    # Object 3: Font
    add_obj(b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>\nendobj\n")

    page_obj_nums = []
    for page in page_lines:
        # Build content stream
        stream_lines = ["BT", "/F1 10 Tf", "50 750 Td", "12 TL"]
        for line in page:
            safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if safe.startswith("# "):
                stream_lines.append("/F1 14 Tf")
                stream_lines.append(f"({safe[2:]}) Tj T*")
                stream_lines.append("/F1 10 Tf")
            elif safe.startswith("## "):
                stream_lines.append("/F1 12 Tf")
                stream_lines.append(f"({safe[3:]}) Tj T*")
                stream_lines.append("/F1 10 Tf")
            else:
                stream_lines.append(f"({safe}) Tj T*")
        stream_lines.append("ET")
        stream_data = "\n".join(stream_lines).encode("latin-1", errors="replace")

        content_num = add_obj(
            f"{len(objects)} 0 obj\n<< /Length {len(stream_data)} >>\nstream\n".encode()
            + stream_data
            + b"\nendstream\nendobj\n"
        )

        _page_num = add_obj(
            f"{len(objects)} 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_num} 0 R /Resources << /Font << /F1 3 0 R >> >> >>\nendobj\n".encode()
        )
        page_obj_nums.append(len(objects))

    # Fix pages object
    kids = " ".join(f"{n} 0 R" for n in page_obj_nums)
    objects[pages_obj_idx - 1] = f"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {len(page_obj_nums)} >>\nendobj\n".encode()

    # Build PDF
    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    for i, obj in enumerate(objects):
        offsets.append(buf.tell())
        buf.write(obj)

    xref_start = buf.tell()
    buf.write(b"xref\n")
    buf.write(f"0 {len(objects) + 1}\n".encode())
    buf.write(b"0000000000 65535 f \n")
    for offset in offsets:
        buf.write(f"{offset:010d} 00000 n \n".encode())

    buf.write(b"trailer\n")
    buf.write(f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode())
    buf.write(b"startxref\n")
    buf.write(f"{xref_start}\n".encode())
    buf.write(b"%%EOF\n")

    return buf.getvalue()
