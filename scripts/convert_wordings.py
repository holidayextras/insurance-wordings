#!/usr/bin/env python3
"""Convert the insurance wording archive into deterministic Markdown files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import unicodedata
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".jpg", ".jpeg"}
WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SHEET_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PACKAGE_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slug(value: str) -> str:
    normalised = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", normalised.lower()).strip("-") or "document"


def markdown_identity(relative_source: Path) -> tuple[str, Path]:
    parts = [slug(part) for part in relative_source.with_suffix("").parts]
    wording_id = "/".join(parts)
    return wording_id, Path("wordings", *parts[:-1], f"{parts[-1]}.md")


def normalise_text(value: str) -> str:
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
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
    lines: list[str] = []
    for line in value.splitlines():
        line = line.expandtabs(4).rstrip()
        stripped = line.lstrip()
        for marker in ("•", "●", "▪", "◦"):
            if stripped.startswith(marker):
                bullet = stripped[len(marker):].strip()
                line = f"- {bullet}" if bullet else ""
                break
        lines.append(line)
    value = "\n".join(lines)
    value = re.sub(r"\n{4,}", "\n\n\n", value)
    return value.strip()


def extract_pdf(path: Path) -> tuple[str, dict[str, Any]]:
    process = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or f"pdftotext exited {process.returncode}")
    pages = [normalise_text(page) for page in process.stdout.split("\f") if page.strip()]
    body = "\n\n".join(f"## Page {index}\n\n{page}" for index, page in enumerate(pages, 1))
    return body, {"pages": len(pages), "hadExtractionWarnings": bool(process.stderr.strip())}


def word_text(element: ET.Element) -> str:
    output: list[str] = []
    for node in element.iter():
        if node.tag in {f"{WORD_NS}t", f"{WORD_NS}delText"} and node.text:
            output.append(node.text)
        elif node.tag == f"{WORD_NS}tab":
            output.append("\t")
        elif node.tag in {f"{WORD_NS}br", f"{WORD_NS}cr"}:
            output.append("\n")
    return normalise_text("".join(output))


def extract_docx(path: Path) -> tuple[str, dict[str, Any]]:
    blocks: list[str] = []
    comments: list[str] = []
    with zipfile.ZipFile(path) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        body = document.find(f".//{WORD_NS}body")
        if body is not None:
            for child in body:
                if child.tag == f"{WORD_NS}p":
                    text = word_text(child)
                    if text:
                        blocks.append(text)
                elif child.tag == f"{WORD_NS}tbl":
                    table_rows: list[str] = []
                    for row in child.findall(f"{WORD_NS}tr"):
                        cells = [word_text(cell).replace("\n", " ") for cell in row.findall(f"{WORD_NS}tc")]
                        if any(cells):
                            table_rows.append(" | ".join(cells))
                    if table_rows:
                        blocks.append("\n".join(table_rows))

        if "word/comments.xml" in archive.namelist():
            comments_root = ET.fromstring(archive.read("word/comments.xml"))
            for comment in comments_root.findall(f"{WORD_NS}comment"):
                text = word_text(comment)
                if not text:
                    continue
                author = comment.attrib.get(f"{WORD_NS}author", "Unknown")
                date = comment.attrib.get(f"{WORD_NS}date", "")
                comments.append(f"- **{author}**{f' ({date})' if date else ''}: {text}")

    body_text = "\n\n".join(blocks)
    if comments:
        body_text += "\n\n## Document comments\n\n" + "\n".join(comments)
    return normalise_text(body_text), {"comments": len(comments)}


def column_index(cell_reference: str) -> int:
    letters = re.match(r"[A-Z]+", cell_reference.upper())
    if not letters:
        return 0
    result = 0
    for character in letters.group(0):
        result = result * 26 + ord(character) - ord("A") + 1
    return result - 1


def shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(f"{SHEET_NS}t")) for item in root.findall(f"{SHEET_NS}si")]


def cell_value(cell: ET.Element, strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{SHEET_NS}t"))
    value = cell.find(f"{SHEET_NS}v")
    if value is None or value.text is None:
        formula = cell.find(f"{SHEET_NS}f")
        return f"={formula.text}" if formula is not None and formula.text else ""
    if cell_type == "s":
        index = int(value.text)
        return strings[index] if index < len(strings) else value.text
    if cell_type == "b":
        return "TRUE" if value.text == "1" else "FALSE"
    return value.text


def markdown_table(rows: list[list[str]]) -> str:
    if not rows:
        return "_Empty sheet._"
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    escaped = [[cell.replace("|", "\\|").replace("\n", "<br>") for cell in row] for row in padded]
    header = escaped[0]
    if not any(header):
        header = [f"Column {index + 1}" for index in range(width)]
    return "\n".join([
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
        *("| " + " | ".join(row) + " |" for row in escaped[1:]),
    ])


def extract_xlsx(path: Path) -> tuple[str, dict[str, Any]]:
    sections: list[str] = []
    with zipfile.ZipFile(path) as archive:
        strings = shared_strings(archive)
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            relationship.attrib["Id"]: relationship.attrib["Target"]
            for relationship in relationships.findall(f"{PACKAGE_REL_NS}Relationship")
        }
        sheets = workbook.find(f"{SHEET_NS}sheets")
        if sheets is None:
            return "_Workbook has no sheets._", {"sheets": 0}
        for sheet in sheets.findall(f"{SHEET_NS}sheet"):
            name = sheet.attrib.get("name", "Sheet").strip()
            relation_id = sheet.attrib.get(f"{REL_NS}id")
            target = targets.get(relation_id or "", "")
            target = target.lstrip("/")
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            root = ET.fromstring(archive.read(target))
            rows: list[list[str]] = []
            for row in root.findall(f".//{SHEET_NS}row"):
                values: list[str] = []
                for cell in row.findall(f"{SHEET_NS}c"):
                    index = column_index(cell.attrib.get("r", "A1"))
                    while len(values) <= index:
                        values.append("")
                    values[index] = cell_value(cell, strings)
                while values and values[-1] == "":
                    values.pop()
                if values:
                    rows.append(values)
            sections.append(f"## {name}\n\n{markdown_table(rows)}")
    return "\n\n".join(sections), {"sheets": len(sections)}


def extract_image(path: Path) -> tuple[str, dict[str, Any]]:
    process = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)], capture_output=True, text=True)
    width = re.search(r"pixelWidth: (\d+)", process.stdout)
    height = re.search(r"pixelHeight: (\d+)", process.stdout)
    dimensions = f"{width.group(1)} × {height.group(1)}" if width and height else "Unknown"
    return (
        "This source is a cover image rather than a text document. No wording text was available to extract.\n\n"
        f"- **Original filename:** {path.name}\n"
        f"- **Dimensions:** {dimensions}",
        {"dimensions": dimensions},
    )


def extract(path: Path) -> tuple[str, dict[str, Any]]:
    extension = path.suffix.lower()
    if extension == ".pdf":
        return extract_pdf(path)
    if extension == ".docx":
        return extract_docx(path)
    if extension == ".xlsx":
        return extract_xlsx(path)
    if extension in {".jpg", ".jpeg"}:
        return extract_image(path)
    raise ValueError(f"Unsupported extension: {extension}")


def frontmatter(metadata: dict[str, Any]) -> str:
    lines = ["---"]
    for key, value in metadata.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)


def convert(source_root: Path, output_root: Path) -> list[dict[str, Any]]:
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)

    sources = sorted(
        path for path in source_root.rglob("*")
        if path.is_file() and path.name != ".DS_Store" and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    manifest: list[dict[str, Any]] = []
    used_outputs: set[Path] = set()
    hashes: dict[str, str] = {}

    for index, source in enumerate(sources, 1):
        relative_source = source.relative_to(source_root)
        wording_id, relative_output = markdown_identity(relative_source)
        source_hash = sha256(source)
        if relative_output in used_outputs:
            relative_output = relative_output.with_stem(f"{relative_output.stem}-{source_hash[:8]}")
            wording_id = f"{wording_id}-{source_hash[:8]}"
        used_outputs.add(relative_output)

        output = output_root.parent / relative_output
        output.parent.mkdir(parents=True, exist_ok=True)
        title = source.stem.strip()
        error: str | None = None
        details: dict[str, Any] = {}
        try:
            content, details = extract(source)
        except Exception as exception:  # Preserve an indexed Markdown record for failed sources.
            error = str(exception)
            content = f"_Extraction failed: {error}_"

        duplicate_of = hashes.get(source_hash)
        hashes.setdefault(source_hash, wording_id)
        metadata = {
            "id": wording_id,
            "title": title,
            "source_path": relative_source.as_posix(),
            "source_format": source.suffix.lower().lstrip("."),
            "source_sha256": source_hash,
            "duplicate_of": duplicate_of,
        }
        output.write_text(
            f"{frontmatter(metadata)}\n\n# {title}\n\n{content.strip()}\n",
            encoding="utf-8",
        )
        manifest.append({
            "id": wording_id,
            "title": title,
            "path": relative_output.as_posix(),
            "sourcePath": relative_source.as_posix(),
            "sourceFormat": source.suffix.lower().lstrip("."),
            "sourceSha256": source_hash,
            "duplicateOf": duplicate_of,
            "characters": len(content),
            "error": error,
            **details,
        })
        print(f"[{index}/{len(sources)}] {relative_source} -> {relative_output}")

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()

    source_root = args.source.expanduser().resolve()
    repo_root = args.repo.expanduser().resolve()
    manifest = convert(source_root, repo_root / "wordings")
    (repo_root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    summary = {
        "documents": len(manifest),
        "failed": sum(1 for item in manifest if item["error"]),
        "duplicates": sum(1 for item in manifest if item["duplicateOf"]),
        "characters": sum(item["characters"] for item in manifest),
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
