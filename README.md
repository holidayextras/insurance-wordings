# Insurance wordings

Markdown versions of Holiday Extras insurance policy wordings and the supporting documents supplied with them.

## Repository structure

- `wordings/` mirrors the source archive using URL-safe directory and file names.
- `manifest.json` maps every stable wording ID to its Markdown file and original source path.
- `scripts/convert_wordings.py` performs the deterministic conversion from PDF, DOCX, XLSX and image sources.

The Markdown frontmatter records the stable ID, original filename, source format and SHA-256 hash. Exact duplicate source files are retained and linked through `duplicate_of` because their original position in the archive may be meaningful.

## Regenerating

```sh
python3 scripts/convert_wordings.py "/path/to/Wordings"
```

PDF text is extracted with `pdftotext`. Word paragraphs, tables and comments are read directly from the DOCX package. Spreadsheet sheets are rendered as Markdown tables. Cover images receive an indexed Markdown metadata record because they contain no policy wording text.

## Using from stories

Stories should retrieve only the wording or relevant section they require through the `llm-fn-proxy` integration. Do not insert the complete archive into a story prompt.
