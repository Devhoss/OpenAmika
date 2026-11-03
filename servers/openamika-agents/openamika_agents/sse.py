"""Minimal Server-Sent Events reader over an iterator of lines (httpx `iter_lines()` or a list)."""
from __future__ import annotations


def iter_sse(lines):
    """Yield (event_name, data) pairs. Comment lines (`: keepalive`) are skipped; multi-line
    `data:` fields are joined with newlines, as the spec says."""
    event, data = None, []
    for raw in lines:
        if raw is None:
            continue
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = None, []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, "\n".join(data)
