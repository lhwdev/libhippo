"""Knowledge formatting utilities for XML presentation, slicing, and frontmatter handling."""

from __future__ import annotations

import re
from typing import Literal

import yaml


def process_frontmatter_and_title(
    content: str,
    mode: Literal["stripped", "essential", "raw"] = "raw",
) -> str:
    """Process YAML frontmatter according to mode ('stripped', 'essential', 'raw').

    - 'raw': preserves content as-is.
    - 'stripped': removes frontmatter completely; includes/merges frontmatter title into H1.
    - 'essential': preserves only 'version', 'last_updated', 'related' frontmatter keys; includes/merges title into H1.
    """
    if mode == "raw":
        return content

    fm_match = re.match(r"^\s*---\s*\n(.*?)\n\s*---\s*(?:\n|\Z)(.*)", content, flags=re.DOTALL)
    if not fm_match:
        return content

    fm_text = fm_match.group(1)
    body = fm_match.group(2)

    try:
        fm_data = yaml.safe_load(fm_text)
        if not isinstance(fm_data, dict):
            fm_data = {}
    except Exception:
        fm_data = {}

    title = fm_data.get("title")
    if isinstance(title, (str, int)):
        title_str = str(title).strip()
        h1_match = re.search(r"^#\s+(.+?)\s*$", body, flags=re.MULTILINE)
        if h1_match:
            existing_h1 = h1_match.group(1).strip()
            t_low = title_str.lower()
            e_low = existing_h1.lower()
            if t_low in e_low or e_low in t_low:
                winner = title_str if len(title_str) >= len(existing_h1) else existing_h1
                body = body[:h1_match.start()] + f"# {winner}" + body[h1_match.end():]
            else:
                body = f"# {title_str}\n\n{body.lstrip()}"
        else:
            body = f"# {title_str}\n\n{body.lstrip()}"

    body = body.strip()

    if mode == "stripped":
        return body

    essential_keys = ["version", "last_updated", "related"]
    essential_dict = {
        k: fm_data[k] for k in essential_keys if k in fm_data and fm_data[k] is not None
    }
    if not essential_dict:
        return body

    fm_yaml = yaml.dump(essential_dict, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{fm_yaml}\n---\n{body}"


def format_knowledge_document(
    path: str,
    content: str,
    total_lines: int | None = None,
    start_line: int = 1,
    end_line: int | None = None,
    frontmatter: Literal["stripped", "essential", "raw"] = "raw",
    with_line_numbers: bool = False,
) -> str:
    """Format knowledge content as <knowledge path="..." total_lines="..."> XML block."""
    if frontmatter != "raw":
        content = process_frontmatter_and_title(content, mode=frontmatter)

    lines = content.splitlines()
    actual_count = len(lines)
    tot = total_lines if total_lines is not None else actual_count
    s_line = max(1, start_line)
    e_line = min(tot, end_line) if end_line is not None else tot

    if total_lines is not None and total_lines != actual_count:
        if with_line_numbers:
            selected_text = "\n".join(f"{idx}: {line}" for idx, line in enumerate(lines, start=s_line))
        else:
            selected_text = content
    else:
        if s_line > actual_count:
            selected_text = ""
        else:
            selected = lines[s_line - 1 : e_line]
            if with_line_numbers:
                selected_text = "\n".join(f"{idx}: {line}" for idx, line in enumerate(selected, start=s_line))
            else:
                selected_text = "\n".join(selected)

    lines_attr = f' lines="{s_line}:{e_line}"' if (s_line > 1 or e_line < tot or with_line_numbers) else ""
    return f'<knowledge path="{path}"{lines_attr} total_lines="{tot}">\n{selected_text}\n</knowledge>'
