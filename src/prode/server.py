"""MCP server exposing prode projects and data points as tools and resources."""

import functools
import json
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from pydantic import Field

from . import datapoints as dp
from . import projects as pj
from .errors import ProdeError
from .store import Store

INSTRUCTIONS = """\
prode stores projects as directories of data points (e.g. candidates, vendors, papers).
Each data point has metadata (fields defined by the project's schema), a SUMMARY.md with a
fixed section layout, and optional attached files.

How to work:
1. Start with list_projects, then load_project to read the project's OVERVIEW, schema and
   which filter operators each field supports.
2. Discover data points with search / list_all_data. Filtering runs server-side over the
   whole project, so never page through everything to find matches yourself; use filters,
   and request only the `fields` you need.
3. Whenever the user refers to a data point by name (possibly misspelled or partial), call
   resolve_data_point first and use the returned slug. If several candidates are close,
   ask the user which one they mean.
4. get_data_point returns SUMMARY.md, which is the primary description. Only read attached
   files (read_file) when SUMMARY.md or the user points to them.
5. Writes (add/update/delete, files) are validated against the schema and committed to git.
"""

Project = Annotated[str, Field(description="Project slug (from list_projects).")]
Slug = Annotated[str, Field(description="Data point slug (use resolve_data_point to find it from a name).")]
Limit = Annotated[int, Field(ge=1, le=200, description="Max rows to return (1-200).")]
Offset = Annotated[int, Field(ge=0, description="Rows to skip, for pagination.")]
Fields = Annotated[
    list[str] | None,
    Field(description="Only include these fields in each row (slug and name are always included)."),
]
SortBy = Annotated[str | None, Field(description="Field to sort by; prefix with '-' for descending.")]


def _tool_errors(fn):
    """Turn expected domain errors into ToolErrors so the model sees the message."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ProdeError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


def _resource_errors(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ProdeError as exc:
            raise ResourceError(str(exc)) from exc

    return wrapper


def build_server(store: Store) -> MCPServer:
    mcp = MCPServer(name="prode", instructions=INSTRUCTIONS)

    def tool(fn):
        return mcp.tool()(_tool_errors(fn))

    # ---- projects --------------------------------------------------------------------

    @tool
    def list_projects() -> dict[str, Any]:
        """List all projects with their display name and number of data points."""
        return {"projects": pj.list_projects(store)}

    @tool
    def create_project(
        name: Annotated[str, Field(description="Human-readable project name; its slug becomes the directory name.")],
        overview: Annotated[str, Field(description="Markdown for OVERVIEW.md: purpose, conventions, how data is organised.")],
        fields: Annotated[
            dict[str, Any] | None,
            Field(description=(
                "Metadata schema: {field_name: {type, required?, values?, description?}}. "
                "type is one of string, number, integer, boolean, date, enum, list. "
                "enum fields need 'values'. Field names are lowercase snake_case; "
                "slug, name, created_at and updated_at are reserved."
            )),
        ] = None,
        summary_sections: Annotated[
            list[str] | None,
            Field(description="Ordered SUMMARY.md section titles shared by every data point (default: Summary, Notes)."),
        ] = None,
        slug: Annotated[str | None, Field(description="Optional explicit project slug.")] = None,
    ) -> dict[str, Any]:
        """Create a new project with its OVERVIEW.md, schema.json and SUMMARY.md template."""
        return pj.create_project(store, name, overview, fields, summary_sections, slug)

    @tool
    def load_project(project: Project) -> dict[str, Any]:
        """Load a project's OVERVIEW.md, schema, SUMMARY.md template, data point count and the
        filter operators each field supports. Call this before working in a project."""
        return pj.load_project(store, project)

    @tool
    def update_project_overview(
        project: Project,
        overview: Annotated[str, Field(description="Full replacement markdown for OVERVIEW.md.")],
    ) -> dict[str, Any]:
        """Replace a project's OVERVIEW.md."""
        return pj.update_project_overview(store, project, overview)

    @tool
    def update_schema(
        project: Project,
        add_fields: Annotated[
            dict[str, Any] | None, Field(description="New optional fields, same format as create_project.fields.")
        ] = None,
        add_enum_values: Annotated[
            dict[str, list[str]] | None, Field(description="{enum_field: [new values]} to append.")
        ] = None,
        add_summary_sections: Annotated[
            list[str] | None, Field(description="New SUMMARY.md sections appended at the end.")
        ] = None,
    ) -> dict[str, Any]:
        """Evolve a project schema in non-breaking ways only (add optional fields, enum values,
        summary sections)."""
        return pj.update_schema(store, project, add_fields, add_enum_values, add_summary_sections)

    # ---- discovery -------------------------------------------------------------------

    @tool
    def list_all_data(
        project: Project,
        limit: Limit = 20,
        offset: Offset = 0,
        fields: Fields = None,
        sort_by: SortBy = None,
    ) -> dict[str, Any]:
        """List data points (index rows: slug, name, updated_at and metadata), paginated."""
        return dp.list_all_data(store, project, limit, offset, fields, sort_by)

    @tool
    def search(
        project: Project,
        keyword: Annotated[
            str | None,
            Field(description="Case-insensitive substring matched against slug, name and string/enum/list fields."),
        ] = None,
        filters: Annotated[
            dict[str, Any] | None,
            Field(description=(
                'Field conditions, all of which must match. {"status": "interview"} means equals; '
                '{"status": ["a","b"]} means in; {"experience": {"gte": 3}} uses an operator. '
                "Operators: eq, ne, in, nin, gt, gte, lt, lte, contains, exists (see load_project "
                "for which apply to each field). For list fields a bare value means contains."
            )),
        ] = None,
        sort_by: SortBy = None,
        limit: Limit = 20,
        offset: Offset = 0,
        fields: Fields = None,
    ) -> dict[str, Any]:
        """Search a project's data points by keyword and/or metadata filters. Returns the total
        match count and one page of rows."""
        return dp.search(store, project, keyword, filters, sort_by, limit, offset, fields)

    @tool
    def resolve_data_point(
        project: Project,
        name: Annotated[str, Field(description="Name or partial/misspelled name the user mentioned.")],
        limit: Annotated[int, Field(ge=1, le=20)] = 5,
    ) -> dict[str, Any]:
        """Fuzzy-match a name to data point slugs, best first with a 0-100 score. An exact
        name/slug match returns score 100."""
        return dp.resolve_data_point(store, project, name, limit)

    # ---- data points -----------------------------------------------------------------

    @tool
    def add_data_point(
        project: Project,
        name: Annotated[str, Field(description="Display name, e.g. 'Tarun Singh'.")],
        meta: Annotated[
            dict[str, Any] | None, Field(description="Metadata values matching the project schema.")
        ] = None,
        summary: Annotated[
            dict[str, str] | None,
            Field(description="{section title: markdown} for the project's SUMMARY.md sections. Use ### for sub-headings."),
        ] = None,
        slug: Annotated[str | None, Field(description="Optional explicit slug; generated from name otherwise.")] = None,
    ) -> dict[str, Any]:
        """Create a data point: its directory, meta.json and SUMMARY.md."""
        return dp.add_data_point(store, project, name, meta, summary, slug)

    @tool
    def get_data_point(project: Project, slug: Slug) -> dict[str, Any]:
        """Get a data point's metadata, SUMMARY.md and file list."""
        return dp.get_data_point(store, project, slug)

    @tool
    def update_data_point(
        project: Project,
        slug: Slug,
        name: Annotated[str | None, Field(description="New display name (slug does not change).")] = None,
        meta: Annotated[
            dict[str, Any] | None,
            Field(description="Partial metadata patch; set a field to null to remove it."),
        ] = None,
        summary: Annotated[
            dict[str, str] | None,
            Field(description="{section title: markdown}; only the given sections are replaced."),
        ] = None,
    ) -> dict[str, Any]:
        """Update a data point's name, metadata and/or SUMMARY.md sections."""
        return dp.update_data_point(store, project, slug, name, meta, summary)

    @tool
    def delete_data_point(project: Project, slug: Slug) -> dict[str, Any]:
        """Archive a data point (moved to the project's .archive/ directory, kept in git history)."""
        return dp.delete_data_point(store, project, slug)

    @tool
    def reindex(project: Project) -> dict[str, Any]:
        """Rebuild the project's metadata.json index from every data point's meta.json."""
        return dp.reindex(store, project)

    # ---- files -----------------------------------------------------------------------

    @tool
    def add_file(
        project: Project,
        slug: Slug,
        source_path: Annotated[str, Field(description="Absolute path of a local file to copy in.")],
        description: Annotated[str, Field(description="One line describing the file; shown in SUMMARY.md's Files section.")],
        filename: Annotated[str | None, Field(description="Name to store it under (default: source file name).")] = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Attach a local file to a data point and list it in SUMMARY.md."""
        return dp.add_file(store, project, slug, source_path, description, filename, overwrite)

    @tool
    def list_files(project: Project, slug: Slug) -> dict[str, Any]:
        """List a data point's attached files with sizes and descriptions."""
        return dp.list_files(store, project, slug)

    @tool
    def read_file(
        project: Project,
        slug: Slug,
        filename: str,
        max_chars: Annotated[int, Field(ge=1, le=200_000)] = dp.DEFAULT_MAX_CHARS,
    ) -> dict[str, Any]:
        """Read an attached text file (truncated to max_chars). For binary files (PDF, zip,
        images) returns the absolute path and type instead of content."""
        return dp.read_file(store, project, slug, filename, max_chars)

    @tool
    def remove_file(project: Project, slug: Slug, filename: str) -> dict[str, Any]:
        """Delete an attached file from a data point and from SUMMARY.md."""
        return dp.remove_file(store, project, slug, filename)

    # ---- resources -------------------------------------------------------------------

    @mcp.resource("prode://projects", mime_type="application/json", description="All projects.")
    def projects_resource() -> str:
        return json.dumps(pj.list_projects(store), indent=2)

    @mcp.resource("prode://{project}/overview", mime_type="text/markdown", description="A project's OVERVIEW.md.")
    @_resource_errors
    def overview_resource(project: str) -> str:
        return pj.load_project(store, project)["overview"]

    @mcp.resource("prode://{project}/schema", mime_type="application/json", description="A project's schema.json.")
    @_resource_errors
    def schema_resource(project: str) -> str:
        return json.dumps(pj.load_project(store, project)["schema"], indent=2)

    @mcp.resource(
        "prode://{project}/{slug}/summary", mime_type="text/markdown", description="A data point's SUMMARY.md."
    )
    @_resource_errors
    def summary_resource(project: str, slug: str) -> str:
        return dp.get_data_point(store, project, slug)["summary"]

    return mcp
