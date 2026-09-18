"""The MCP server end to end, in process, against the fixture snapshot."""

import dataclasses
import json
import logging

import httpx
import pytest
import respx
from mcp import Client

import fixture_snapshot as fx
from glendale_gis.core.catalog import GEOCODER
from glendale_gis.core.config import Settings
from glendale_gis.core.snapshot import Snapshot
from glendale_gis.server import build_state, create_server

GEOCODE_URL = f"{GEOCODER.url}/findAddressCandidates"
TOOLS = {
    "list_datasets",
    "describe_dataset",
    "query_dataset",
    "geocode_address",
    "hazards_at_location",
    "wildfire_zone",
    "flood_zone",
    "seismic_zones",
    "dam_inundation",
    "debris_flow",
    "nearest_resources",
    "read_guide",
}


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(
        Settings.from_env({}), min_request_interval_s=0.0, max_retries=0, cache_dir=tmp_path
    )


@pytest.fixture
def server(tmp_path, settings):
    snapshot = Snapshot.load(fx.write(tmp_path / "snap"))
    return create_server(settings, build_state(settings, snapshot))


async def call(server, name, args):
    async with Client(server) as client:
        result = await client.call_tool(name, args)
    text = result.content[0].text
    return result, json.loads(text)


async def test_every_tool_is_listed_read_only_and_described(server):
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert t.annotations.read_only_hint is True, t.name
        assert t.annotations.destructive_hint is False, t.name
        assert len(t.description or "") > 100, t.name
        assert t.output_schema, t.name


async def test_hazards_at_location_by_coordinates(server):
    result, text = await call(
        server, "hazards_at_location", {"location": {"lat": 34.145, "lon": -118.245}}
    )
    assert not result.is_error
    data = result.structured_content
    assert data["dam_inundation"]["status"] == "in_zone"
    assert data["landslide"]["status"] == "unavailable"
    assert data["flood"]["_meta"]["source"] == "FEMA"  # the wire uses the _meta name
    assert text["location"]["source"] == "coordinates"
    assert "reason" not in text["flood"]  # the text form leaves out nulls


@respx.mock
async def test_address_is_geocoded(server):
    respx.get(GEOCODE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "address": "1 MAIN ST",
                        "score": 100,
                        "location": {"x": -118.26, "y": 34.16},
                        "attributes": {"Addr_type": "PointAddress"},
                    }
                ]
            },
        )
    )
    result, _ = await call(server, "wildfire_zone", {"location": {"address": "1 Main St"}})
    data = result.structured_content
    assert data["status"] == "in_zone"
    assert data["location"]["matched_address"] == "1 MAIN ST"
    assert data["location"]["score"] == 100


@respx.mock
async def test_ambiguous_address_is_an_error_with_candidates(server):
    both = [
        {
            "address": "613 E BROADWAY",
            "score": 91,
            "location": {"x": -118.25, "y": 34.16},
            "attributes": {"Addr_type": "PointAddress"},
        },
        {
            "address": "613 W BROADWAY",
            "score": 91,
            "location": {"x": -118.265, "y": 34.16},
            "attributes": {"Addr_type": "PointAddress"},
        },
    ]
    respx.get(GEOCODE_URL).mock(return_value=httpx.Response(200, json={"candidates": both}))
    result, text = await call(server, "flood_zone", {"location": {"address": "613 Broadway"}})
    assert result.is_error
    assert text["error"] == "The address matches more than one place in Glendale."
    assert [c["address"] for c in text["candidates"]] == ["613 E BROADWAY", "613 W BROADWAY"]
    assert text["suggestions"]


async def test_bad_location_is_rejected(server):
    async with Client(server) as client:
        result = await client.call_tool("flood_zone", {"location": {"lat": 34.1}})
    assert result.is_error
    assert "both lat and lon" in result.content[0].text


async def test_query_dataset_and_its_errors(server):
    result, _ = await call(
        server,
        "query_dataset",
        {"dataset": "bus_stops", "filters": [{"field": "Route", "op": "contains", "value": "7"}]},
    )
    assert result.structured_content["total"] == 2
    result, text = await call(
        server,
        "query_dataset",
        {"dataset": "bus_stops", "filters": [{"field": "route", "op": "eq", "value": "7"}]},
    )
    assert result.is_error
    assert "Unknown field(s)" in text["error"]


async def test_nearest_resources_and_catalog_tools(server):
    result, _ = await call(
        server,
        "nearest_resources",
        {"location": {"lat": 34.16, "lon": -118.25}, "kinds": ["parks"], "limit": 1},
    )
    assert result.structured_content["groups"][0]["results"][0]["name"] == "CENTRAL PARK"
    result, _ = await call(server, "list_datasets", {})
    assert len(result.structured_content["datasets"]) == 21
    result, _ = await call(server, "describe_dataset", {"dataset": "fema_flood_zones"})
    assert result.structured_content["class_field"] == "FLD_ZONE"


async def test_missing_snapshot_explains_how_to_fix_it(tmp_path, settings):
    settings = dataclasses.replace(settings, snapshot_path=tmp_path / "nowhere")
    server = create_server(settings, build_state(settings))
    result, text = await call(server, "wildfire_zone", {"location": {"lat": 34.16, "lon": -118.25}})
    assert result.is_error
    assert "snapshot isn't available" in text["error"]
    assert "build_snapshot.py" in text["suggestions"][0]


@respx.mock
async def test_addresses_never_reach_the_logs(tmp_path, settings, caplog):
    # httpx logs request URLs at INFO, and geocoder URLs contain the address.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.NOTSET)  # as a fresh process starts
    server = create_server(settings, build_state(settings, Snapshot.load(fx.write(tmp_path / "s"))))
    secret = {"address": "1 SECRET LN", "score": 100, "location": {"x": -118.26, "y": 34.16}}
    respx.get(GEOCODE_URL).mock(return_value=httpx.Response(200, json={"candidates": [secret]}))
    with caplog.at_level(logging.DEBUG):
        await call(server, "geocode_address", {"address": "1 Secret Ln"})
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "secret" not in logged.lower()


RESOURCES = {
    "glendale-gis://about": ("text/markdown", "# Glendale, California GIS MCP Server"),
    "glendale-gis://datasets": ("text/markdown", "# Datasets"),
    "glendale-gis://docs/real-time-sources": ("text/markdown", "# Real-Time Emergency Information"),
    "glendale-gis://docs/snapshot-data": ("text/markdown", "# Reading the Snapshot Data"),
    "glendale-gis://snapshot/manifest": ("application/json", "{"),
}


async def test_resources(server):
    async with Client(server) as client:
        listed = {str(r.uri): r for r in (await client.list_resources()).resources}
        assert set(listed) == set(RESOURCES)
        for uri, (mime, start) in RESOURCES.items():
            assert listed[uri].mime_type == mime
            assert listed[uri].description
            text = (await client.read_resource(uri)).contents[0].text
            assert text.startswith(start), uri
        manifest = json.loads(
            (await client.read_resource("glendale-gis://snapshot/manifest")).contents[0].text
        )
    assert manifest["version"] == 1
    assert "bus_stops" in manifest["layers"]


async def test_manifest_resource_without_a_snapshot(tmp_path, settings):
    settings = dataclasses.replace(settings, snapshot_path=tmp_path / "nowhere")
    async with Client(create_server(settings, build_state(settings))) as client:
        text = (await client.read_resource("glendale-gis://snapshot/manifest")).contents[0].text
    assert "snapshot isn't available" in json.loads(text)["error"]


async def test_about_lists_every_tool_and_the_scope(server):
    async with Client(server) as client:
        about = (await client.read_resource("glendale-gis://about")).contents[0].text
    for tool in TOOLS:
        assert f"| `{tool}` |" in about, tool
    assert "does **not** score or rank risk" in about
    assert about.startswith("# Glendale, California GIS MCP Server")
    assert "City of Glendale, California (Los Angeles County) only" in about
    assert "Arizona" not in about
    assert "Layers unavailable: " in about  # the fixture snapshot is missing some layers
    assert "glendale-gis://docs/real-time-sources" in about


async def test_dataset_template(server):
    async with Client(server) as client:
        templates = (await client.list_resource_templates()).resource_templates
        assert [t.uri_template for t in templates] == ["glendale-gis://datasets/{dataset_id}"]
        page = (await client.read_resource("glendale-gis://datasets/calfire_fhsz_lra")).contents
        missing = (await client.read_resource("glendale-gis://datasets/nope")).contents
    text = page[0].text
    assert text.startswith("# Fire Hazard Severity Zones")
    assert "| `FHSZ` | SmallInteger |" in text
    assert "`-3` = Unzoned, Non Wildland" in text
    assert "Unzoned classes (don't count as in a zone): NonWildland" in text
    assert "Object ID: identifies the feature" in text
    assert "Unknown dataset 'nope'" in missing[0].text


def test_instructions_point_to_the_resources():
    from glendale_gis.server import INSTRUCTIONS

    for uri in [*RESOURCES, "glendale-gis://datasets/{id}"]:
        assert uri in INSTRUCTIONS


@pytest.mark.parametrize(
    "topic, start, uri",
    [
        ("about", "# Glendale, California GIS MCP Server", "glendale-gis://about"),
        ("datasets", "# Datasets", "glendale-gis://datasets"),
        (
            "real_time_sources",
            "# Real-Time Emergency Information",
            "glendale-gis://docs/real-time-sources",
        ),
        ("snapshot_data", "# Reading the Snapshot Data", "glendale-gis://docs/snapshot-data"),
    ],
)
async def test_read_guide_matches_the_resource(server, topic, start, uri):
    async with Client(server) as client:
        result = await client.call_tool("read_guide", {"topic": topic})
        resource = (await client.read_resource(uri)).contents[0].text
    assert not result.is_error
    text = result.content[0].text
    assert text.startswith(start)  # plain Markdown, not JSON
    assert text == resource
    assert result.structured_content["resource_uri"] == uri


async def test_read_guide_rejects_unknown_topics(server):
    async with Client(server) as client:
        result = await client.call_tool("read_guide", {"topic": "secrets"})
    assert result.is_error


def test_instructions_tell_the_model_to_call_read_guide():
    from glendale_gis.server import INSTRUCTIONS

    assert 'read_guide("real_time_sources")' in INSTRUCTIONS
