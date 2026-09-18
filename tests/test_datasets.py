import dataclasses

import httpx
import pytest
import respx

import fixture_snapshot as fx
from glendale_gis.core import datasets
from glendale_gis.core.arcgis import ArcGISClient
from glendale_gis.core.cache import Cache
from glendale_gis.core.catalog import DATASETS, get_dataset
from glendale_gis.core.config import Settings
from glendale_gis.core.models import ActionableError
from glendale_gis.core.query import QueryEngine
from glendale_gis.core.snapshot import Snapshot


@pytest.fixture(scope="module")
def snap(tmp_path_factory):
    return Snapshot.load(fx.write(tmp_path_factory.mktemp("snap")))


@pytest.fixture
async def engine(snap):
    settings = dataclasses.replace(Settings.from_env({}), min_request_interval_s=0.0, max_retries=0)
    async with ArcGISClient(settings) as client:
        yield QueryEngine(snap, client, Cache(None), settings)


def test_list_datasets(snap):
    result = datasets.list_datasets(snap)
    by_id = {d.id: d for d in result.datasets}
    assert set(by_id) == set(DATASETS)
    assert by_id["bus_stops"].feature_count == 3
    assert by_id["bus_stops"].available
    assert by_id["hospitals"].available is False  # not in the fixture snapshot
    assert by_id["parcels"].access == "live"
    assert by_id["parcels"].feature_count is None
    assert by_id["calfire_fhsz_lra"].description.startswith("Wildfire hazard severity zones")
    assert result.snapshot_built_at == "2026-09-18T19:45:16+00:00"
    assert any("real-time" in n for n in result.notes)


async def test_describe_snapshot_dataset(snap, engine):
    d = await datasets.describe_dataset(snap, engine, "calfire_fhsz_lra")
    fields = {f.name: f for f in d.fields}
    assert list(fields) == ["OBJECTID", "SRA", "FHSZ", "FHSZ_Description"]
    assert fields["FHSZ"].type == "esriFieldTypeSmallInteger"
    assert fields["FHSZ"].values["-3"] == "Unzoned, Non Wildland"
    assert "does not mean there is no wildfire risk" in fields["FHSZ_Description"].description
    assert d.unzoned_classes == ["NonWildland"]
    assert d.class_field == "FHSZ_Description"
    assert d.feature_count == 2
    assert d.clip == {"buffer": "hazard", "meters": 2000.0}
    assert d.source_last_edit == "2025-11-19T17:40:00+00:00"
    assert "NonWildland means unzoned, not free of wildfire risk" in d.description
    assert d.meta.cached is True
    assert d.available


async def test_describe_flags_inferred_meanings(snap, engine):
    d = await datasets.describe_dataset(snap, engine, "bus_stops")
    assert {f.name: f.inferred for f in d.fields}["On_Street"] is True


async def test_describe_unavailable_layer_still_documents_fields(snap, engine):
    d = await datasets.describe_dataset(snap, engine, "hospitals")
    assert d.available is False
    assert "not in the snapshot" in d.unavailable_reason
    assert [f.name for f in d.fields][:2] == ["OBJECTID", "NAME"]


@respx.mock
async def test_describe_live_dataset_reads_the_live_schema(snap, engine):
    meta = {
        "fields": [
            {"name": "OBJECTID", "type": "esriFieldTypeOID"},
            {"name": "APN", "type": "esriFieldTypeString", "alias": "Parcel number"},
        ]
    }
    respx.get(get_dataset("parcels").layer_url).mock(return_value=httpx.Response(200, json=meta))
    d = await datasets.describe_dataset(snap, engine, "parcels")
    assert [(f.name, f.alias) for f in d.fields] == [("OBJECTID", None), ("APN", "Parcel number")]
    assert d.access == "live"
    assert d.meta.cached is False


async def test_describe_unknown_dataset(snap, engine):
    with pytest.raises(ActionableError, match="Unknown dataset 'nope'"):
        await datasets.describe_dataset(snap, engine, "nope")


def test_catalog_markdown_groups_datasets(snap):
    text = datasets.catalog_markdown(snap)
    for heading in (
        "## Hazard zones",
        "## Community resources",
        "## Reference layers",
        "## Queried live",
    ):
        assert heading in text
    assert "| `bus_stops` | **Beeline Bus Stops.**" not in text  # no description: title only
    assert "| `bus_stops` | Beeline Bus Stops | City of Glendale | 3 | not published |" in text
    assert "| `hospitals` |" in text and "| unavailable |" in text
    assert "| `parcels` | **Parcels.** 54,300 parcels" in text
    assert "2025-11-19 |" in text  # hazard layers show the source's last edit date


async def test_dataset_markdown(snap, engine):
    text = datasets.dataset_markdown(await datasets.describe_dataset(snap, engine, "bus_stops"))
    assert "| `On_Street` | String | Street the stop is on" in text
    assert "*(inferred)*" in text
    assert "`NB` = Northbound" in text
    assert "- Covers: Glendale plus 100 m" in text
