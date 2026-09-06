from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    Flowable,
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


NAVY = colors.HexColor("#102A43")
BLUE = colors.HexColor("#1479FF")
CYAN = colors.HexColor("#16B8D4")
GREEN = colors.HexColor("#17A673")
INK = colors.HexColor("#263238")
MUTED = colors.HexColor("#627D98")
PALE = colors.HexColor("#EAF2FF")
PALE_GREEN = colors.HexColor("#E8F7F1")
LINE = colors.HexColor("#D9E2EC")
PAPER = colors.HexColor("#F7F9FC")


def register_fonts() -> None:
    regular = Path(r"C:\Windows\Fonts\malgun.ttf")
    bold = Path(r"C:\Windows\Fonts\malgunbd.ttf")
    if not regular.exists() or not bold.exists():
        raise SystemExit("Malgun Gothic fonts are required")
    pdfmetrics.registerFont(TTFont("Malgun", regular))
    pdfmetrics.registerFont(TTFont("Malgun-Bold", bold))


def paragraph_markup(text: str) -> str:
    text = text.replace("**", "")
    escaped = html.escape(text)
    return re.sub(
        r"`([^`]+)`",
        r'<font color="#1479FF">\1</font>',
        escaped,
    )


class ArchitectureDiagram(Flowable):
    def __init__(self, width: float = 440, height: float = 350):
        super().__init__()
        self.width = width
        self.height = height

    def draw(self) -> None:
        canvas = self.canv
        canvas.saveState()
        pairs = [
            ("사용자 질의", "질의 구조화"),
            ("기업·기간 필터", "공시 검색·재순위"),
            ("정정 계보·기준시점", "수치 추출·Decimal 계산"),
            ("Evidence Bundle", "HyperCLOVA X"),
            ("수치·인용·안전 검증", "답변·근거·한계"),
        ]
        box_w = 175
        box_h = 40
        left_x = 25
        right_x = self.width - box_w - 25
        top = self.height - 38
        row_gap = 64

        canvas.setFont("Malgun-Bold", 11)
        canvas.setFillColor(NAVY)
        canvas.drawString(25, self.height - 17, "검증 가능한 공시 답변 파이프라인")

        for index, (left, right) in enumerate(pairs):
            y = top - index * row_gap
            for x, label, fill in (
                (left_x, left, PALE),
                (right_x, right, PALE_GREEN if index >= 3 else colors.white),
            ):
                canvas.setFillColor(fill)
                canvas.setStrokeColor(BLUE if index < 3 else GREEN)
                canvas.roundRect(x, y, box_w, box_h, 8, fill=1, stroke=1)
                canvas.setFillColor(INK)
                canvas.setFont("Malgun-Bold", 8.8)
                canvas.drawCentredString(x + box_w / 2, y + 15, label)

            canvas.setStrokeColor(MUTED)
            canvas.setLineWidth(1.3)
            canvas.line(left_x + box_w + 7, y + box_h / 2, right_x - 7, y + box_h / 2)
            canvas.line(right_x - 12, y + box_h / 2 + 4, right_x - 7, y + box_h / 2)
            canvas.line(right_x - 12, y + box_h / 2 - 4, right_x - 7, y + box_h / 2)
            if index < len(pairs) - 1:
                next_y = top - (index + 1) * row_gap + box_h
                canvas.line(right_x + box_w / 2, y - 7, right_x + box_w / 2, next_y + 7)
                canvas.line(right_x + box_w / 2 - 4, next_y + 12, right_x + box_w / 2, next_y + 7)
                canvas.line(right_x + box_w / 2 + 4, next_y + 12, right_x + box_w / 2, next_y + 7)
                canvas.setStrokeColor(CYAN)
                canvas.line(right_x + box_w / 2, next_y, left_x + box_w / 2, next_y)
                canvas.line(left_x + box_w / 2, next_y, left_x + box_w / 2, next_y - 7)

        canvas.setFillColor(MUTED)
        canvas.setFont("Malgun", 7.8)
        canvas.drawString(25, 10, "원본 DB·overlay·index는 read-only, 검증 실패는 fallback 또는 답변 보류")
        canvas.restoreState()


class ProposalCanvas(pdfcanvas.Canvas):
    def __init__(self, *args, **kwargs):
        kwargs["invariant"] = 1
        super().__init__(*args, **kwargs)
        self._page_states: list[dict[str, object]] = []

    def showPage(self) -> None:
        self._page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        for page_number, state in enumerate(self._page_states, start=1):
            self.__dict__.update(state)
            if page_number > 1:
                self._draw_running_elements(page_number)
            super().showPage()
        super().save()

    def _draw_running_elements(self, page_number: int) -> None:
        width, height = A4
        self.saveState()
        self.setStrokeColor(LINE)
        self.line(20 * mm, height - 15 * mm, width - 20 * mm, height - 15 * mm)
        self.setFont("Malgun-Bold", 7.5)
        self.setFillColor(NAVY)
        self.drawString(20 * mm, height - 11.5 * mm, "MIRA · 근거 검증형 공시 AI Agent")
        self.setFont("Malgun", 7.5)
        self.setFillColor(MUTED)
        self.drawRightString(width - 20 * mm, 10 * mm, str(page_number))
        self.drawString(20 * mm, 10 * mm, "2026 미래에셋증권 AI Festival · 기술제안서")
        self.restoreState()


def build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "cover_title": ParagraphStyle(
            "CoverTitle", parent=base["Title"], fontName="Malgun-Bold", fontSize=27,
            leading=36, textColor=NAVY, alignment=TA_LEFT, spaceAfter=10,
        ),
        "cover_sub": ParagraphStyle(
            "CoverSub", parent=base["Normal"], fontName="Malgun", fontSize=13,
            leading=21, textColor=MUTED,
        ),
        "h1": ParagraphStyle(
            "H1", parent=base["Heading1"], fontName="Malgun-Bold", fontSize=18,
            leading=25, textColor=NAVY, spaceBefore=2, spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "H2", parent=base["Heading2"], fontName="Malgun-Bold", fontSize=13,
            leading=19, textColor=BLUE, spaceBefore=9, spaceAfter=7,
        ),
        "body": ParagraphStyle(
            "Body", parent=base["BodyText"], fontName="Malgun", fontSize=9.2,
            leading=15.2, textColor=INK, wordWrap="CJK", spaceAfter=7,
        ),
        "bullet": ParagraphStyle(
            "Bullet", parent=base["BodyText"], fontName="Malgun", fontSize=8.9,
            leading=14.2, textColor=INK, leftIndent=14, firstLineIndent=-9,
            bulletIndent=4, wordWrap="CJK", spaceAfter=3,
        ),
        "code": ParagraphStyle(
            "Code", parent=base["Code"], fontName="Malgun", fontSize=7.5,
            leading=11.5, textColor=NAVY, backColor=PAPER, borderColor=LINE,
            borderWidth=0.5, borderPadding=7, wordWrap="CJK", spaceAfter=8,
        ),
        "small": ParagraphStyle(
            "Small", parent=base["BodyText"], fontName="Malgun", fontSize=7.5,
            leading=11, textColor=MUTED, wordWrap="CJK",
        ),
        "status": ParagraphStyle(
            "Status", parent=base["BodyText"], fontName="Malgun-Bold", fontSize=9,
            leading=14, textColor=GREEN, alignment=TA_CENTER,
        ),
        "caption": ParagraphStyle(
            "Caption", parent=base["BodyText"], fontName="Malgun", fontSize=8,
            leading=12, textColor=MUTED, alignment=TA_CENTER, wordWrap="CJK",
            spaceBefore=4, spaceAfter=10,
        ),
    }


def styled_table(rows: list[list[str]], available_width: float, styles: dict[str, ParagraphStyle]) -> Table:
    count = max(len(row) for row in rows)
    normalized = [row + [""] * (count - len(row)) for row in rows]
    data = [
        [Paragraph(paragraph_markup(cell), styles["small"]) for cell in row]
        for row in normalized
    ]
    if count == 2:
        widths = [available_width * 0.34, available_width * 0.66]
    elif count == 3:
        widths = [available_width * 0.25, available_width * 0.5, available_width * 0.25]
    else:
        widths = [available_width / count] * count
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Malgun-Bold"),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PAPER]),
        ("GRID", (0, 0), (-1, -1), 0.35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def parse_markdown(path: Path, available_width: float, styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("## 1."))
    lines = lines[start:]
    story: list[Flowable] = []
    index = 0
    paragraph_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph_lines:
            text = " ".join(item.strip() for item in paragraph_lines).strip()
            if text:
                story.append(Paragraph(paragraph_markup(text), styles["body"]))
            paragraph_lines.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped == "<!-- pagebreak -->":
            flush_paragraph()
            story.append(PageBreak())
            index += 1
            continue
        if stripped.startswith("```mermaid"):
            flush_paragraph()
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                index += 1
            story.append(ArchitectureDiagram(width=min(available_width, 440)))
            story.append(Spacer(1, 8))
            index += 1
            continue
        if stripped.startswith("```"):
            flush_paragraph()
            index += 1
            code: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            story.append(Paragraph("<br/>".join(html.escape(item) for item in code), styles["code"]))
            index += 1
            continue
        if stripped.startswith("|"):
            flush_paragraph()
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                    rows.append(cells)
                index += 1
            if rows:
                story.append(styled_table(rows, available_width, styles))
                story.append(Spacer(1, 9))
            continue
        figure = re.match(r"^!\[(.*)\]\(([^)]+)\)$", stripped)
        if figure:
            flush_paragraph()
            caption, source = figure.group(1), figure.group(2)
            resolved = (path.parent / source).resolve()
            if not resolved.exists():
                raise SystemExit(f"missing figure: {resolved}")
            natural_width, natural_height = ImageReader(str(resolved)).getSize()
            width = min(available_width, natural_width * 0.72)
            height = width * natural_height / natural_width
            if height > 205 * mm:
                height = 205 * mm
                width = height * natural_width / natural_height
            block: list[Flowable] = [Image(str(resolved), width=width, height=height)]
            if caption:
                block.append(Paragraph(paragraph_markup(caption), styles["caption"]))
            story.append(KeepTogether(block))
            index += 1
            continue
        heading = re.match(r"^(#{2,3})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            level = "h1" if len(heading.group(1)) == 2 else "h2"
            if level == "h1":
                story.append(Spacer(1, 4 * mm))
            story.append(Paragraph(paragraph_markup(heading.group(2)), styles[level]))
            index += 1
            continue
        bullet = re.match(r"^-\s+(.+)$", stripped)
        numbered = re.match(r"^(\d+)\.\s+(.+)$", stripped)
        if bullet or numbered:
            flush_paragraph()
            label = "•" if bullet else f"{numbered.group(1)}."
            body = bullet.group(1) if bullet else numbered.group(2)
            story.append(Paragraph(f"{label} {paragraph_markup(body)}", styles["bullet"]))
            index += 1
            continue
        if not stripped:
            flush_paragraph()
            index += 1
            continue
        paragraph_lines.append(stripped)
        index += 1
    flush_paragraph()
    return story


def cover_story(styles: dict[str, ParagraphStyle]) -> list[Flowable]:
    status = Table(
        [[Paragraph("평가용 End-point · http://101.79.31.221:8000/answer", styles["status"])]],
        colWidths=[155 * mm],
    )
    status.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE_GREEN),
        ("BOX", (0, 0), (-1, -1), 0.7, GREEN),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    facts = Table([
        ["38.8GB", "300/300", "0건", "946 tests"],
        ["immutable corpus", "deterministic stress", "hard safety failures", "Python regression"],
    ], colWidths=[38.75 * mm] * 4)
    facts.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Malgun-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 13),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BACKGROUND", (0, 1), (-1, 1), PAPER),
        ("TEXTCOLOR", (0, 1), (-1, 1), MUTED),
        ("FONTNAME", (0, 1), (-1, 1), "Malgun"),
        ("FONTSIZE", (0, 1), (-1, 1), 7),
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return [
        Spacer(1, 36 * mm),
        Paragraph("MIRA", styles["cover_title"]),
        Paragraph("근거 검증형 공시 AI Agent", styles["cover_title"]),
        Spacer(1, 7 * mm),
        Paragraph(
            "제10회 2026 미래에셋증권 AI Festival · 공시 Agent 기술제안서"
            "<br/>팀명 신비복숭아 · 김세민 · 이정찬 · 이정민",
            styles["cover_sub"],
        ),
        Spacer(1, 17 * mm),
        status,
        Spacer(1, 12 * mm),
        facts,
        Spacer(1, 34 * mm),
        Paragraph("시간·정정·단위·근거를 잃지 않는 공시 전용 Agent", styles["h2"]),
        Paragraph(
            "검증된 evidence bundle만 HyperCLOVA X에 전달하고, 숫자·인용·안전 계약을 통과한 답변만 사용자에게 제공합니다.",
            styles["body"],
        ),
        Spacer(1, 10 * mm),
        Paragraph("작성 기준일 2026-09-06", styles["small"]),
        PageBreak(),
    ]


def first_page(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(NAVY)
    canvas.rect(0, A4[1] - 9 * mm, A4[0], 9 * mm, fill=1, stroke=0)
    canvas.setFillColor(BLUE)
    canvas.rect(0, 0, A4[0], 5 * mm, fill=1, stroke=0)
    canvas.restoreState()


def later_pages(canvas, doc) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setStrokeColor(LINE)
    canvas.line(20 * mm, height - 15 * mm, width - 20 * mm, height - 15 * mm)
    canvas.setFont("Malgun-Bold", 7.5)
    canvas.setFillColor(NAVY)
    canvas.drawString(20 * mm, height - 11.5 * mm, "MIRA · 근거 검증형 공시 AI Agent")
    canvas.setFont("Malgun", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(width - 20 * mm, 10 * mm, f"{doc.page}")
    canvas.drawString(20 * mm, 10 * mm, "2026 미래에셋증권 AI Festival · 기술제안서")
    canvas.restoreState()


def build_pdf(source: Path, output: Path) -> None:
    register_fonts()
    output.parent.mkdir(parents=True, exist_ok=True)
    styles = build_styles()
    doc = SimpleDocTemplate(
        str(output),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title="MIRA 근거 검증형 공시 AI Agent 기술제안서",
        author="신비복숭아 (김세민 · 이정찬 · 이정민)",
        subject="제10회 2026 미래에셋증권 AI Festival 공시 Agent",
    )
    story = cover_story(styles)
    story.extend(parse_markdown(source, doc.width, styles))
    doc.build(story, onFirstPage=first_page, canvasmaker=ProposalCanvas)

    reader = PdfReader(str(output))
    if len(reader.pages) < 10:
        raise SystemExit(f"unexpected page count: {len(reader.pages)}")
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    for required in ("MIRA", "300/300", "HyperCLOVA X", "제출 항목 대응표"):
        if required not in extracted:
            raise SystemExit(f"missing PDF text: {required}")
    print(f"wrote={output} pages={len(reader.pages)} bytes={output.stat().st_size}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("docs/submission/technical-proposal.md"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/submission/technical-proposal.pdf"),
    )
    args = parser.parse_args()
    build_pdf(args.source, args.output)


if __name__ == "__main__":
    main()
