import re

from .colors import C
from .llm import clean_text

def render_markdown(text: str, colors: C) -> str:
    """Render common Markdown constructs with ANSI colors, without dependencies."""
    text = clean_text(text)
    rendered = []
    in_code = False
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if (
            index + 1 < len(lines)
            and "|" in line
            and re.match(r"^\s*\|?\s*:?-{3,}", lines[index + 1])
            and "|" in lines[index + 1]
        ):
            table = []
            while index < len(lines) and "|" in lines[index].strip():
                cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                    table.append(cells)
                index += 1
            if table:
                column_count = max(len(row) for row in table)
                table = [row + [""] * (column_count - len(row)) for row in table]
                widths = [max(len(row[col]) for row in table) for col in range(column_count)]
                rendered.append("+" + "+".join("-" * (width + 2) for width in widths) + "+")
                for row_index, row in enumerate(table):
                    rendered.append(
                        "| " + " | ".join(cell.ljust(widths[col]) for col, cell in enumerate(row)) + " |"
                    )
                    if row_index == 0:
                        rendered.append("+" + "+".join("-" * (width + 2) for width in widths) + "+")
                rendered.append("+" + "+".join("-" * (width + 2) for width in widths) + "+")
            continue
        if line.strip().startswith("```"):
            in_code = not in_code
            if not in_code:
                rendered.append("")
            continue
        if in_code:
            rendered.append(colors(line, C.CYAN))
            continue
        line = re.sub(
            r"^(#{1,6})\s+(.+)$",
            lambda match: colors(match.group(2), C.BOLD, C.BLUE),
            line,
        )
        line = re.sub(
            r"\*\*(.+?)\*\*",
            lambda match: colors(match.group(1), C.BOLD),
            line,
        )
        line = re.sub(
            r"`([^`]+)`",
            lambda match: colors(match.group(1), C.YELLOW),
            line,
        )
        line = re.sub(r"^\s*[-*]\s+", "• ", line)
        rendered.append(line)
        index += 1
    return "\n".join(rendered)

