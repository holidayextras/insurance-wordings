#!/usr/bin/env python3
"""Refresh the customer-facing policy documents as readable web Markdown.

The archive converter intentionally preserves PDF layout. That is useful for
search, but it produces page headings and indented blocks that render badly in
the customer UI. This script is deliberately limited to the current live
documents and converts them into semantic, responsive Markdown without
changing their wording.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


LIVE_DIRECTORY = Path("Live Policy Documents/22-07-2026 - Onwards")
OUTPUT_DIRECTORY = Path("wordings/live-policy-documents/22-07-2026-onwards")


@dataclass(frozen=True)
class Document:
    source_name: str
    output_name: str
    title: str
    kind: str
    remove_opening_lines: tuple[str, ...]


DOCUMENTS = (
    Document(
        "ERGO0726_FDstacked.pdf",
        "ergo0726-fdstacked.md",
        "Holiday Extras Travel Insurance Policy Wording",
        "policy",
        ("Holiday Extras", "Travel Insurance", "Policy Wording"),
    ),
    *(
        Document(
            f"ERGOIPID{tier}0726_FDstacked.pdf",
            f"ergoipid{tier.lower()}0726-fdstacked.md",
            f"{tier} Travel Insurance Product Information",
            "ipid",
            ("Travel Insurance", "Insurance Product Information Document"),
        )
        for tier in ("Bronze", "Silver", "Gold")
    ),
    *(
        Document(
            f"PGSDirect{tier}0726.pdf",
            f"pgsdirect{tier.lower()}0726.md",
            f"{tier} Travel Insurance Policy Benefits and Limits",
            "schedule",
            ("Holiday Extras", "Travel Insurance"),
        )
        for tier in ("Bronze", "Silver", "Gold")
    ),
)


H2_HEADINGS = {
    "how to get the help you need",
    "anywheregp",
    "welcome",
    "how to make a claim",
    "summary of your cover",
    "summary of your policy limits",
    "get to know your policy before you travel",
    "pre-existing medical conditions and if your health changes",
    "reciprocal health agreements",
    "words with special meanings",
    "what this policy doesn’t cover",
    "what this policy doesn't cover",
    "other important information",
    "appendix: your policy tables",
    "activities and sports that we can and can’t cover",
    "countries that we do cover",
    "flight delay cover",
    "additional information",
    "guide to your travel insurance documents",
    "these are your policy benefits and limits (this is what you get):",
    "your insurers",
    "medical conditions existing before you bought your policy",
}

H3_HEADINGS = {
    "emergency assistance and claims contact numbers",
    "your documents and how to check your cover",
    "accessible format documents",
    "your policy limits and excesses",
    "about your insurance contract",
    "countries we do and don’t cover",
    "how long can your trips last",
    "what you need to know:",
    "what is this type of insurance?",
    "what type of insurance is this?",
    "what is insured?",
    "what is not insured?",
    "are there any restrictions on cover?",
    "where am i covered?",
    "what are my obligations?",
    "when and how do i pay?",
    "when does the cover start and end?",
    "how do i cancel the contract?",
    "where does the insurance apply?",
    "what obligations do i have?",
    "when does insurance start and end?",
    "how do i cancel my insurance?",
    "limitations of insurance?",
    "key contact telephone numbers",
    "criteria for purchase",
    "make yourself heard",
}

LABELS = ("Company", "Product", "Insurer", "Tel", "Email", "Web", "Write", "WhatsApp")
FOOTER_PATTERNS = (
    re.compile(r"^www\.holidayextras\.co\.uk$", re.IGNORECASE),
    re.compile(r"^Less hassle\. More holiday\.$", re.IGNORECASE),
    re.compile(r"^Designed by Oak Creative\b", re.IGNORECASE),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pdf_pages(path: Path, *, mode: str = "default") -> list[str]:
    command = ["pdftotext"]
    if mode == "layout":
        command.append("-layout")
    elif mode == "raw":
        command.append("-raw")
    command.extend(["-enc", "UTF-8", str(path), "-"])
    process = subprocess.run(command, check=False, capture_output=True, text=True, timeout=300)
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or f"pdftotext exited {process.returncode}")
    return [page for page in process.stdout.split("\f") if page.strip()]


def normalise_characters(value: str) -> str:
    replacements = {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\u00a0": " ",
        "\r\n": "\n",
        "\r": "\n",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)


def remove_page_furniture(lines: list[str], page_number: int) -> list[str]:
    non_empty = [index for index, line in enumerate(lines) if line.strip()]
    candidates = non_empty[:3] + non_empty[-4:]
    for index in reversed(candidates):
        if lines[index].strip() == str(page_number):
            lines[index] = ""
            break

    return [
        line
        for line in lines
        if not any(pattern.match(line.strip()) for pattern in FOOTER_PATTERNS)
    ]


def strip_opening_lines(lines: list[str], expected: tuple[str, ...]) -> list[str]:
    remaining = list(lines)
    for expected_line in expected:
        while remaining and not remaining[0].strip():
            remaining.pop(0)
        if remaining and remaining[0].strip().casefold() == expected_line.casefold():
            remaining.pop(0)
    return remaining


def markdown_line(
    line: str,
    *,
    kind: str,
    pending_bullet: bool,
    allow_h2: bool,
) -> tuple[str, bool]:
    line = re.sub(r"\s+", " ", line.strip())
    if not line:
        return "", pending_bullet

    if line in {"•", "●", "▪", "◦"} or (kind == "ipid" and line in {"!", "y"}):
        return "", True

    bullet_item = re.match(r"^[•●▪◦]\s*(.+)$", line)
    if bullet_item:
        line = f"- {bullet_item.group(1)}"

    if kind == "ipid":
        icon_item = re.match(r"^(?:3|2|!|y)\s+(.+)$", line)
        if icon_item:
            line = f"- {icon_item.group(1)}"

    if pending_bullet and not line.startswith("-"):
        line = f"- {line}"
        pending_bullet = False

    heading = line.casefold()
    if allow_h2 and heading in H2_HEADINGS:
        return f"## {line.rstrip(':')}", pending_bullet
    if heading in H3_HEADINGS:
        return f"### {line.rstrip(':')}", pending_bullet

    for label in LABELS:
        prefix = f"{label}:"
        if line.casefold().startswith(prefix.casefold()):
            return f"**{line[:len(prefix)]}**{line[len(prefix):]}", pending_bullet

    return line, pending_bullet


def clean_page(page: str, page_number: int, document: Document) -> str:
    raw_lines = normalise_characters(page).splitlines()
    raw_lines = remove_page_furniture(raw_lines, page_number)
    if page_number == 1:
        raw_lines = strip_opening_lines(raw_lines, document.remove_opening_lines)

    return format_lines(raw_lines, document)


def format_lines(raw_lines: list[str], document: Document) -> str:

    output: list[str] = []
    pending_bullet = False
    seen_content = False
    for raw_line in raw_lines:
        line, pending_bullet = markdown_line(
            raw_line,
            kind=document.kind,
            pending_bullet=pending_bullet,
            allow_h2=not seen_content,
        )
        output.append(line)
        seen_content = seen_content or bool(line)

    text = "\n".join(output)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def escape_table_cell(value: str) -> str:
    return value.replace("|", "\\|").strip()


def schedule_table(source: Path) -> str:
    rows: list[list[str]] = []
    for page in pdf_pages(source, mode="layout")[:2]:
        lines = normalise_characters(page).splitlines()
        header_index = next((index for index, line in enumerate(lines) if "Cover description" in line), None)
        if header_index is None:
            continue
        header = lines[header_index]
        second_column = header.index("We will pay up to:")
        third_column = header.index("Excess*:")

        for line in lines[header_index + 1 :]:
            stripped = line.strip()
            if not stripped or any(pattern.match(stripped) for pattern in FOOTER_PATTERNS):
                continue
            if stripped.startswith("*"):
                break

            cells = [
                line[:second_column].strip(),
                line[second_column:third_column].strip(),
                line[third_column:].strip(),
            ]
            split_cells = re.split(r"\s{2,}", stripped, maxsplit=2)
            if len(split_cells) == 3:
                cells = split_cells
            if not any(cells):
                continue

            if not cells[0] and rows:
                for index, value in enumerate(cells[1:], 1):
                    if value:
                        rows[-1][index] = f"{rows[-1][index]} {value}".strip()
                continue

            rows.append(cells)

    rendered = [
        "| Cover | We will pay up to | Excess |",
        "| --- | --- | --- |",
    ]
    for cover, limit, excess in rows:
        if cover and not limit and not excess:
            cover = f"**{cover}**"
        rendered.append(
            f"| {escape_table_cell(cover)} | {escape_table_cell(limit)} | {escape_table_cell(excess)} |"
        )
    return "\n".join(rendered)


def schedule_footnotes(page: str, document: Document) -> str:
    lines = normalise_characters(page).splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip().startswith("* Unless")), None)
    if start is None:
        return ""
    return clean_page("\n".join(lines[start:]), 2, document)


def render_schedule(source: Path, document: Document) -> str:
    pages = pdf_pages(source)
    first_page_before_table = pages[0].split("Cover description", 1)[0]
    intro = clean_page(first_page_before_table, 1, document)
    footnotes = schedule_footnotes(pages[1], document) if len(pages) > 1 else ""
    remaining_pages = [
        clean_page(page, index, document)
        for index, page in enumerate(pages[2:], 3)
    ]
    sections = [
        intro,
        "## Policy benefits and limits\n\n" + schedule_table(source),
        footnotes,
        *remaining_pages,
    ]
    return f"# {document.title}\n\n" + "\n\n".join(section for section in sections if section) + "\n"


def render_ipid_page(page: str, page_number: int, document: Document) -> str:
    raw_lines = normalise_characters(page).splitlines()
    raw_lines = remove_page_furniture(raw_lines, page_number)
    if page_number == 1:
        raw_lines = strip_opening_lines(raw_lines, document.remove_opening_lines)

    heading_index = next(
        (
            index
            for index, line in enumerate(raw_lines)
            if "What is insured?" in line and "What is not insured?" in line
        ),
        None,
    )
    if heading_index is None:
        return format_lines(raw_lines, document)

    heading_line = raw_lines[heading_index]
    split_at = heading_line.index("What is not insured?")
    intro = format_lines(raw_lines[:heading_index], document)
    left = format_lines([line[:split_at] for line in raw_lines[heading_index + 1 :]], document)
    right = format_lines([line[split_at:] for line in raw_lines[heading_index + 1 :]], document)
    sections = [
        intro,
        "### What is insured?\n\n" + left,
        "### What is not insured?\n\n" + right,
    ]
    return "\n\n".join(section for section in sections if section)


def render_flight_delay_ipid_page(page: str, page_number: int, document: Document) -> str:
    lines = remove_page_furniture(normalise_characters(page).splitlines(), page_number)

    flight_delay_exclusions = (
        "The use of nuclear, chemical, or biological weapons of mass",
        "War or Terrorism",
        "Any government imposing travel restrictions",
        "Pandemic or epidemic.",
    )
    lines = [
        f"• {line.strip()}" if line.strip().startswith(flight_delay_exclusions) else line
        for line in lines
    ]

    def find(text: str) -> int:
        return next(index for index, line in enumerate(lines) if line.strip().startswith(text))

    insured_heading = find("What is insured?")
    insured_start = find("Flights registered at least")
    uninsured_start = find("Flights registered less")
    insured_continues = find("Compensation for a qualifying delay")
    uninsured_continues = find("Any flight that is cancelled")

    intro = format_lines(lines[:insured_heading], document)
    insured = format_lines(
        lines[insured_start:uninsured_start] + lines[insured_continues:uninsured_continues],
        document,
    )
    uninsured = format_lines(
        lines[uninsured_start:insured_continues] + lines[uninsured_continues:],
        document,
    )
    return "\n\n".join((
        intro,
        "### What is insured?\n\n" + insured,
        "### What is not insured?\n\n" + uninsured,
    ))


def render_ipid(source: Path, document: Document) -> str:
    layout_pages = pdf_pages(source, mode="layout")
    reading_order_pages = pdf_pages(source)
    rendered_pages: list[str] = []
    for index, page in enumerate(reading_order_pages, 1):
        if index == 1:
            rendered_pages.append(render_ipid_page(layout_pages[0], index, document))
        elif index == 3:
            rendered_pages.append(render_flight_delay_ipid_page(page, index, document))
        else:
            rendered_pages.append(clean_page(page, index, document))
    body = "\n\n".join(rendered_pages)
    return f"# {document.title}\n\n{body}\n"


def render_document(source: Path, document: Document) -> str:
    if document.kind == "schedule":
        return render_schedule(source, document)
    if document.kind == "ipid":
        return render_ipid(source, document)
    pages = pdf_pages(source, mode="raw" if document.kind == "policy" else "default")
    rendered_pages = [clean_page(page, index, document) for index, page in enumerate(pages, 1)]
    rendered_pages = [page for page in rendered_pages if page]
    body = "\n\n".join(rendered_pages)
    return f"# {document.title}\n\n{body}\n"


def replace_frontmatter(markdown: str, document: Document, source: Path) -> str:
    body_start = markdown.find("\n---", 3)
    if not markdown.startswith("---\n") or body_start == -1:
        raise ValueError(f"Missing frontmatter in {document.output_name}")
    frontmatter = markdown[: body_start + 4]
    frontmatter = re.sub(
        r'^title:.*$',
        f'title: {json.dumps(document.title, ensure_ascii=False)}',
        frontmatter,
        flags=re.MULTILINE,
    )
    expected_hash = sha256(source)
    if f'source_sha256: "{expected_hash}"' not in frontmatter:
        raise ValueError(f"Source hash changed for {document.source_name}; review the new PDF before publishing")
    return frontmatter


def update_manifest(repo: Path, updates: dict[str, tuple[str, int]]) -> None:
    path = repo / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for item in manifest:
        update = updates.get(item["path"])
        if update:
            item["title"], item["characters"] = update
            found.add(item["path"])
    missing = set(updates) - found
    if missing:
        raise ValueError(f"Live documents missing from manifest: {sorted(missing)}")
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    source_root = args.source_root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    updates: dict[str, tuple[str, int]] = {}

    for document in DOCUMENTS:
        source = source_root / LIVE_DIRECTORY / document.source_name
        output = repo / OUTPUT_DIRECTORY / document.output_name
        if not source.is_file():
            raise FileNotFoundError(source)
        existing = output.read_text(encoding="utf-8")
        body = render_document(source, document)
        output.write_text(f"{replace_frontmatter(existing, document, source)}\n\n{body}", encoding="utf-8")
        relative_output = output.relative_to(repo).as_posix()
        updates[relative_output] = (document.title, len(body))
        print(f"Refreshed {relative_output}")

    update_manifest(repo, updates)


if __name__ == "__main__":
    main()
