from __future__ import annotations


def excerpt_text(
    text: str,
    *,
    markers: list[str],
    max_lines: int = 18,
    context_lines: int = 2,
) -> str:
    lines = text.splitlines()
    if not lines:
        return ""
    lowered_markers = [marker.lower() for marker in markers if marker]
    start_index = 0
    for index, line in enumerate(lines):
        lowered = line.lower()
        if any(marker in lowered for marker in lowered_markers):
            start_index = max(index - context_lines, 0)
            break
    end_index = min(len(lines), start_index + max_lines)
    excerpt = "\n".join(lines[start_index:end_index]).rstrip()
    if end_index < len(lines):
        excerpt = f"{excerpt}\n..."
    return excerpt


def build_reference_example(
    label: str,
    text: str,
    *,
    markers: list[str],
    comment_prefix: str,
    max_lines: int = 18,
) -> str:
    excerpt = excerpt_text(text, markers=markers, max_lines=max_lines)
    if not excerpt:
        return f"{comment_prefix} {label}"
    return f"{comment_prefix} {label}\n{excerpt}"


def compact_log_lines(
    text: str,
    *,
    prefix: str,
    max_lines: int = 3,
    max_chars: int = 220,
) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if len(line) > max_chars:
            line = f"{line[: max_chars - 3]}..."
        lines.append(f"{prefix}: {line}")
        if len(lines) >= max_lines:
            break
    return lines
