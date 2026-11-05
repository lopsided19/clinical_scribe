"""Author the <=10 page project description. Development-only dependency: reportlab."""
from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, KeepTogether, HRFlowable

ROOT = Path(__file__).resolve().parents[1]


def build():
    pdfmetrics.registerFont(TTFont("CNSong", "C:/Windows/Fonts/simsun.ttc", subfontIndex=0))
    pdfmetrics.registerFont(TTFont("CNHei", "C:/Windows/Fonts/simhei.ttf"))
    target = ROOT / "docs/PROJECT_DESCRIPTION.pdf"
    width, height = A4
    blue = colors.HexColor("#126b7a")
    dark = colors.HexColor("#203747")
    text = ParagraphStyle("body", fontName="CNSong", fontSize=10.5, leading=17,
                          textColor=dark, wordWrap="CJK", spaceAfter=8, alignment=TA_LEFT)
    heading = ParagraphStyle("heading", fontName="CNHei", fontSize=14, leading=21,
                             textColor=blue, wordWrap="CJK", spaceBefore=13, spaceAfter=8, keepWithNext=True)
    title = ParagraphStyle("title", fontName="CNHei", fontSize=23, leading=33,
                           textColor=dark, wordWrap="CJK", spaceAfter=14)
    subtitle = ParagraphStyle("subtitle", parent=text, textColor=colors.HexColor("#526b78"), fontSize=10, leading=16)
    status = ParagraphStyle("status", parent=text, backColor=colors.HexColor("#edf5f6"),
                            borderColor=colors.HexColor("#c9dfe3"), borderWidth=0.5, borderPadding=10, spaceAfter=15)
    doc = BaseDocTemplate(str(target), pagesize=A4, leftMargin=48, rightMargin=48,
                          topMargin=60, bottomMargin=48, title="临床智录｜项目说明", author="参赛项目工作稿")

    def page(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#c9dce1"))
        canvas.line(48, height - 38, width - 48, height - 38)
        canvas.setFont("CNHei", 8)
        canvas.setFillColor(blue)
        canvas.drawString(48, height - 29, "临床智录  /  方法与工程说明")
        canvas.setFillColor(colors.HexColor("#687d87"))
        canvas.drawString(48, 28, "2026.11.05  ·  真实模型验证待完成")
        canvas.drawRightString(width - 48, 28, str(document.page))
        canvas.restoreState()

    frame = Frame(48, 48, width - 96, height - 108, leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id="main", frames=frame, onPage=page))
    story = [Paragraph("临床智录", title),
             Paragraph("基于原文台账与来源校验的<br/>住院首次病程记录智能生成", heading),
             Paragraph("第二届北京市大学生“人工智能+”创新大赛 · 本地工程版本说明", subtitle),
             Spacer(1, 6), HRFlowable(width="100%", thickness=2, color=blue), Spacer(1, 15),
             Paragraph("交付状态：已完成公开数据准备、参赛程序与本地工程验证。尚无赛方实际模型接口；本文不报告真实 Qwen 医学效果或改进收益。", status)]
    markdown = (ROOT / "docs/PROJECT_DESCRIPTION.md").read_text(encoding="utf-8")
    pending_heading = None
    for block in re.split(r"\n\s*\n", markdown):
        block = block.strip()
        if not block or block.startswith("# "):
            continue
        if block.startswith("## "):
            if pending_heading is not None:
                story.append(pending_heading)
            pending_heading = Paragraph(html.escape(block[3:]), heading)
        else:
            pieces = block.splitlines() if re.match(r"\d+\. ", block) else [block]
            for piece in pieces:
                clean = html.escape(piece.replace("\n", " ").replace("`", ""))
                paragraph = Paragraph(clean, text)
                story.append(KeepTogether([pending_heading, paragraph] if pending_heading is not None else [paragraph]))
                pending_heading = None
    if pending_heading is not None:
        story.append(pending_heading)
    doc.build(story)
    print(target)


if __name__ == "__main__":
    build()
