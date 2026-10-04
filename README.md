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

## Tools

| Area | Tools |
|---|---|
| Projects | `list_projects`, `create_project`, `load_project`, `update_project_overview`, `update_schema` |
| Discovery | `list_all_data`, `search`, `resolve_data_point`, `reindex` |
| Data points | `add_data_point`, `get_data_point`, `update_data_point`, `delete_data_point` |
| Files | `add_file`, `list_files`, `read_file`, `remove_file` |

Search filters: `{"status": "interview"}` (eq), `{"status": ["a", "b"]}` (in),
`{"experience": {"gte": 3}}`. Operators: `eq ne in nin gt gte lt lte contains exists`.

Resources: `prode://projects`, `prode://{project}/overview`, `prode://{project}/schema`,
`prode://{project}/{slug}/summary`.

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
