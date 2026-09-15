#!/usr/bin/env python3
"""Render a Markdown file to PDF using fpdf2 (pure Python, no system deps).

Supports the subset of Markdown used in the Bluey docs: headings, paragraphs,
bullet/numbered lists, fenced code blocks, tables, blockquotes, horizontal
rules, and inline **bold** / `code` / _italic_ emphasis.

Usage:
    python scripts/md_to_pdf.py <input.md> <output.pdf>
"""
import re
import sys

from fpdf import FPDF

MARGIN = 18
CODE_BG = (245, 245, 245)
TABLE_HEADER_BG = (60, 90, 150)
TABLE_HEADER_FG = (255, 255, 255)
TABLE_ROW_ALT = (238, 242, 248)
RULE_COLOR = (200, 200, 200)


class PDF(FPDF):
    def header(self):
        pass

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", size=8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")
        self.set_text_color(0, 0, 0)


def sanitize(text: str) -> str:
    """Replace characters the built-in Latin-1 fonts cannot encode."""
    replacements = {
        "\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
        "\u2013": "-", "\u2014": "--", "\u2026": "...", "\u2022": "-",
        "\u00a0": " ", "\u2192": "->", "\u25ba": ">", "\u2500": "-",
        "\u2502": "|", "\u250c": "+", "\u2510": "+", "\u2514": "+",
        "\u2518": "+", "\u251c": "+", "\u2524": "+", "\u252c": "+",
        "\u2534": "+", "\u253c": "+", "\u2554": "+", "\u2557": "+",
        "\u255a": "+", "\u255d": "+", "\u25c4": "<", "\u2b07": "v",
        "\u2764": "*",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return text.encode("latin-1", "replace").decode("latin-1")


def strip_inline(text: str) -> str:
    """Remove markdown emphasis markers for plain rendering."""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"\1 (\2)", text)
    return text


def write_inline(pdf: PDF, text: str, size: int = 10.5, line_h: float = 6.0):
    """Write a paragraph honoring **bold** and `code` runs."""
    tokens = re.split(r"(\*\*.+?\*\*|`.+?`)", text)
    for tok in tokens:
        if tok.startswith("**") and tok.endswith("**"):
            pdf.set_font("Helvetica", style="B", size=size)
            pdf.write(line_h, sanitize(tok[2:-2]))
        elif tok.startswith("`") and tok.endswith("`"):
            pdf.set_font("Courier", size=size - 0.5)
            pdf.set_text_color(180, 40, 40)
            pdf.write(line_h, sanitize(tok[1:-1]))
            pdf.set_text_color(0, 0, 0)
        else:
            pdf.set_font("Helvetica", size=size)
            pdf.write(line_h, sanitize(strip_inline(tok)))
    pdf.ln(line_h)


def render_table(pdf: PDF, rows):
    if not rows:
        return
    header, body = rows[0], rows[1:]
    ncols = len(header)
    usable = pdf.w - 2 * MARGIN
    col_w = usable / ncols
    line_h = 5.5

    def row_height(cells):
        lines = 1
        pdf.set_font("Helvetica", size=9)
        for c in cells:
            w = pdf.get_string_width(sanitize(strip_inline(c)))
            lines = max(lines, max(1, int(w // (col_w - 4)) + 1))
        return line_h * lines

    # header
    pdf.set_font("Helvetica", style="B", size=9)
    pdf.set_fill_color(*TABLE_HEADER_BG)
    pdf.set_text_color(*TABLE_HEADER_FG)
    h = row_height(header)
    if pdf.get_y() + h > pdf.h - 20:
        pdf.add_page()
    x0, y0 = pdf.get_x(), pdf.get_y()
    for i, cell in enumerate(header):
        pdf.multi_cell(col_w, line_h, sanitize(strip_inline(cell)), border=1,
                       align="L", fill=True, max_line_height=line_h,
                       new_x="RIGHT", new_y="TOP")
    pdf.set_xy(x0, y0 + h)
    pdf.set_text_color(0, 0, 0)

    # body
    for r, cells in enumerate(body):
        cells = cells + [""] * (ncols - len(cells))
        h = row_height(cells)
        if pdf.get_y() + h > pdf.h - 20:
            pdf.add_page()
        fill = r % 2 == 1
        if fill:
            pdf.set_fill_color(*TABLE_ROW_ALT)
        x0, y0 = pdf.get_x(), pdf.get_y()
        pdf.set_font("Helvetica", size=9)
        for cell in cells:
            pdf.multi_cell(col_w, line_h, sanitize(strip_inline(cell)),
                           border=1, align="L", fill=fill,
                           max_line_height=line_h, new_x="RIGHT", new_y="TOP")
        pdf.set_xy(x0, y0 + h)
    pdf.ln(3)


def convert(md_path: str, pdf_path: str):
    with open(md_path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    pdf = PDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(MARGIN, MARGIN, MARGIN)
    pdf.add_page()

    i = 0
    while i < len(lines):
        line = lines[i]

        # fenced code block
        if line.strip().startswith("```"):
            code = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            pdf.ln(1)
            pdf.set_font("Courier", size=8.5)
            pdf.set_fill_color(*CODE_BG)
            for cl in code:
                if pdf.get_y() > pdf.h - 22:
                    pdf.add_page()
                pdf.cell(0, 4.6, sanitize(cl.replace("\t", "    ")),
                         fill=True, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)
            continue

        # table
        if "|" in line and i + 1 < len(lines) and re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", lines[i + 1]):
            rows = []
            while i < len(lines) and "|" in lines[i]:
                raw = lines[i].strip().strip("|")
                if re.match(r"^[\s:\-|]+$", raw):
                    i += 1
                    continue
                rows.append([c.strip() for c in raw.split("|")])
                i += 1
            render_table(pdf, rows)
            continue

        stripped = line.strip()

        if not stripped:
            pdf.ln(3)
            i += 1
            continue

        # horizontal rule
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", stripped):
            pdf.set_draw_color(*RULE_COLOR)
            y = pdf.get_y() + 1
            pdf.line(MARGIN, y, pdf.w - MARGIN, y)
            pdf.ln(4)
            i += 1
            continue

        # headings
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            text = strip_inline(m.group(2))
            sizes = {1: 20, 2: 15, 3: 12.5, 4: 11}
            size = sizes.get(level, 10.5)
            if pdf.get_y() > pdf.h - 30:
                pdf.add_page()
            pdf.ln(3 if level > 1 else 1)
            pdf.set_font("Helvetica", style="B", size=size)
            pdf.set_text_color(20, 40, 90) if level <= 2 else pdf.set_text_color(40, 60, 100)
            pdf.multi_cell(0, size * 0.5, sanitize(text))
            pdf.set_text_color(0, 0, 0)
            if level <= 2:
                pdf.set_draw_color(*RULE_COLOR)
                y = pdf.get_y() + 1
                pdf.line(MARGIN, y, pdf.w - MARGIN, y)
                pdf.ln(2)
            i += 1
            continue

        # blockquote
        if stripped.startswith(">"):
            text = stripped.lstrip(">").strip()
            pdf.set_fill_color(245, 247, 250)
            pdf.set_draw_color(180, 190, 210)
            pdf.set_font("Helvetica", style="I", size=10)
            pdf.multi_cell(0, 5.5, sanitize(strip_inline(text)), border="L", fill=True)
            pdf.ln(1)
            i += 1
            continue

        # bullet list
        m = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if m:
            indent = len(m.group(1))
            pdf.set_x(MARGIN + 4 + indent)
            pdf.set_font("Helvetica", size=10.5)
            pdf.write(5.5, "-  ")
            write_inline(pdf, m.group(2), size=10.5, line_h=5.5)
            i += 1
            continue

        # numbered list
        m = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if m:
            indent = len(m.group(1))
            pdf.set_x(MARGIN + 4 + indent)
            pdf.set_font("Helvetica", size=10.5)
            pdf.write(5.5, f"{m.group(2)}. ")
            write_inline(pdf, m.group(3), size=10.5, line_h=5.5)
            i += 1
            continue

        # paragraph
        write_inline(pdf, stripped, size=10.5, line_h=6.0)
        i += 1

    pdf.output(pdf_path)
    print(f"Wrote {pdf_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/md_to_pdf.py <input.md> <output.pdf>")
        sys.exit(1)
    convert(sys.argv[1], sys.argv[2])
