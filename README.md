# prode

A filesystem data layer for project data (markdown + JSON + attached files), served to LLM clients through a local MCP server.

## Layout on disk

```
<ROOT>/                         # git repo, one commit per write
  .gitignore                    # ignores .lock and metadata.json
  interviewer-management/       # project (slug)
    OVERVIEW.md                 # what the project is
    schema.json                 # metadata fields + SUMMARY.md sections
    SUMMARY_TEMPLATE.md         # generated from schema
    metadata.json               # derived index, one row per data point (rebuildable)
    .archive/                   # soft-deleted data points
    tarun-singh/                # data point (slug)
      meta.json                 # source of truth: name, metadata, file descriptions
      SUMMARY.md                # fixed section layout + auto-managed "Files" section
      resume.pdf
```

`meta.json` is the source of truth. `metadata.json` is regenerated on every write and is
rebuilt automatically if it is missing or corrupt (or on demand with the `reindex` tool).
Search and filtering run in the server over a cached copy of the index. At 100k data points,
a filtered search takes about 50 ms once the index is cached, so the LLM only ever sees one page of results.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Run / register

```bash
.venv/bin/python -m prode --root ~/prode-data          # stdio server
# or: PRODE_ROOT=~/prode-data .venv/bin/prode

# Claude Code
claude mcp add prode -- "$PWD/.venv/bin/python" -m prode --root ~/prode-data
```

Flags: `--root PATH` (else `$PRODE_ROOT`, else `~/prode-data`), `--no-git`, `--log-level`.

## Schema

```json
{
  "fields": {
    "fullname":   {"type": "string", "required": true},
    "status":     {"type": "enum", "values": ["applied", "interview", "offer"], "required": true},
    "experience": {"type": "number"},
    "tags":       {"type": "list"},
    "applied_on": {"type": "date"}
  },
  "summary_sections": ["Background", "Assessment", "Notes"]
}
```

Types: `string, number, integer, boolean, date, enum, list`. `slug`, `name`, `created_at`
and `updated_at` are reserved. `update_schema` only allows non-breaking changes: adding optional fields, enum values or sections.

## MCP tools

The server exposes 17 tools. Parameters ending in `?` are optional.

### Projects

| Tool | Parameters | What it does |
|---|---|---|
| `list_projects` | none | Lists every project with its display name and number of data points |
| `create_project` | `name`, `overview`, `fields?`, `summary_sections?`, `slug?` | Creates the project directory, OVERVIEW.md, schema.json, SUMMARY_TEMPLATE.md and an empty index |
| `load_project` | `project` | Returns OVERVIEW.md, the schema, the summary template, the data point count and the filter operators each field supports |
| `update_project_overview` | `project`, `overview` | Replaces OVERVIEW.md |
| `update_schema` | `project`, `add_fields?`, `add_enum_values?`, `add_summary_sections?` | Changes the schema without breaking existing data (additions only) |

### Discovery

| Tool | Parameters | What it does |
|---|---|---|
| `list_all_data` | `project`, `limit`, `offset`, `fields?`, `sort_by?` | Returns one page of index rows |
| `search` | `project`, `keyword?`, `filters?`, `sort_by?`, `limit`, `offset`, `fields?` | Keyword search plus typed filters; returns the total match count and one page of rows |
| `resolve_data_point` | `project`, `name`, `limit` | Fuzzy-matches a name (even misspelled) to slugs, best first with a score |
| `reindex` | `project` | Rebuilds metadata.json from every data point's meta.json |

Search filters: `{"status": "interview"}` (eq), `{"status": ["a", "b"]}` (in),
`{"experience": {"gte": 3}}`. Operators: `eq ne in nin gt gte lt lte contains exists`.

### Data points

| Tool | Parameters | What it does |
|---|---|---|
| `add_data_point` | `project`, `name`, `meta?`, `summary?`, `slug?` | Creates the data point's directory, meta.json and SUMMARY.md, after checking the metadata against the schema |
| `get_data_point` | `project`, `slug` | Returns the metadata, SUMMARY.md and file list; if the slug isn't found, suggests close matches |
| `update_data_point` | `project`, `slug`, `name?`, `meta?`, `summary?` | Applies a partial metadata change (a `null` value removes a field) and replaces only the given summary sections |
| `delete_data_point` | `project`, `slug` | Moves the data point to `.archive/` and removes it from the index |

### Files

| Tool | Parameters | What it does |
|---|---|---|
| `add_file` | `project`, `slug`, `source_path`, `description`, `filename?`, `overwrite?` | Copies a local file in and lists it in SUMMARY.md's Files section |
| `list_files` | `project`, `slug` | Lists attached files with size, description and whether they're tracked in meta.json |
| `read_file` | `project`, `slug`, `filename`, `max_chars?` | Returns the content of text files (cut off at `max_chars`); for binary files, returns the path and type instead |
| `remove_file` | `project`, `slug`, `filename` | Deletes the file and removes it from SUMMARY.md |

## MCP resources

| URI | Content |
|---|---|
| `prode://projects` | All projects (JSON) |
| `prode://{project}/overview` | A project's OVERVIEW.md |
| `prode://{project}/schema` | A project's schema.json |
| `prode://{project}/{slug}/summary` | A data point's SUMMARY.md |

## Tests

```bash
.venv/bin/pytest -m "not slow"      # unit + e2e
.venv/bin/pytest -m slow -s         # 100k data point scale check, prints timings
```

## Code map

`src/prode/`: `fsutil` (atomic writes, locks, safe paths) · `slug` · `schema` (validation and
coercion) · `summary` (SUMMARY.md render/parse) · `index` (metadata.json + cache) · `query`
(filters, keyword, sort, fuzzy resolve) · `projects` / `datapoints` (operations) · `gitops` ·
`store` · `server` (MCP tools/resources) · `__main__`.

## License

MIT, see [LICENSE](LICENSE).
