"""Just enough Markdown to show parts of the README inside the web UI.

Handles what the README uses: headings, paragraphs, fenced code, bullet
lists, pipe tables, and inline code, bold and links. Everything is escaped
first, so the output can only ever contain the tags written here.
"""

from __future__ import annotations

import html
import re


def section(markdown: str, heading: str) -> str:
    """The body of one "## heading" section, up to the next "## " heading."""
    match = re.search(r"^## " + re.escape(heading) + r"\s*$", markdown, re.MULTILINE)
    if not match:
        return ""
    rest = markdown[match.end():]
    end = re.search(r"^## ", rest, re.MULTILINE)
    return rest[:end.start()] if end else rest


def _inline(text: str) -> str:
    text = html.escape(text, quote=True)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"\[([^\]]+)\]\((https://[^)\s]+)\)",
                  r'<a href="\2" target="_blank" rel="noopener noreferrer">\1</a>', text)
    return text


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def render(markdown: str) -> str:
    lines = markdown.strip("\n").split("\n")
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            i += 1
            code = []
            while i < len(lines) and not lines[i].startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
        elif re.match(r"#{2,4} ", line):
            level = len(line) - len(line.lstrip("#"))
            out.append(f"<h{level}>{_inline(line[level:].strip())}</h{level}>")
            i += 1
        elif line.startswith("|") and i + 1 < len(lines) and re.match(r"\|[\s:|-]+\|\s*$", lines[i + 1]):
            head = "".join(f"<th>{_inline(cell)}</th>" for cell in _cells(line))
            i += 2
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append("<tr>" + "".join(f"<td>{_inline(cell)}</td>"
                                             for cell in _cells(lines[i])) + "</tr>")
                i += 1
            out.append(f'<table class="table"><thead><tr>{head}</tr></thead>'
                       f'<tbody>{"".join(rows)}</tbody></table>')
        elif line.startswith("- "):
            items = []
            while i < len(lines) and (lines[i].startswith("- ") or
                                      (items and lines[i].startswith("  ") and lines[i].strip())):
                if lines[i].startswith("- "):
                    items.append(lines[i][2:].strip())
                else:
                    items[-1] += " " + lines[i].strip()
                i += 1
            out.append("<ul>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + "</ul>")
        elif not line.strip():
            i += 1
        else:
            paragraph = []
            while i < len(lines) and lines[i].strip() and not re.match(r"```|#{2,4} |- |\|", lines[i]):
                paragraph.append(lines[i].strip())
                i += 1
            out.append("<p>" + _inline(" ".join(paragraph)) + "</p>")
    return "\n".join(out)
