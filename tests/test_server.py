import asyncio

import pytest

from prode.server import build_server

from conftest import INTERVIEW_FIELDS


@pytest.fixture
def server(store):
    return build_server(store)


def call(server, tool_name, **args):
    return asyncio.run(server.call_tool(tool_name, args))


def test_tool_roundtrip(server):
    call(server, "create_project", name="Interviews", overview="Hiring.", fields=INTERVIEW_FIELDS)
    call(server, "add_data_point", project="interviews", name="Tarun Singh",
         meta={"fullname": "Tarun Singh", "status": "interview"})
    res = call(server, "search", project="interviews", filters={"status": "interview"})
    text = str(res)
    assert "tarun-singh" in text


def test_domain_error_is_tool_error(server):
    from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
    with pytest.raises(ToolError) as ei:
        call(server, "load_project", project="missing")
    assert not isinstance(ei.value, UnexpectedToolError)
    assert "not found" in str(ei.value)


def test_resources(server):
    call(server, "create_project", name="Interviews", overview="Hiring.", fields=INTERVIEW_FIELDS)
    call(server, "add_data_point", project="interviews", name="Tarun Singh",
         meta={"fullname": "Tarun Singh", "status": "interview"})
    overview = asyncio.run(server.read_resource("prode://interviews/overview"))
    assert "Hiring." in str(overview)
    summary = asyncio.run(server.read_resource("prode://interviews/tarun-singh/summary"))
    assert "# Tarun Singh" in str(summary)
