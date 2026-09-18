import pytest

from glendale_gis.core import catalog
from glendale_gis.core.arcgis import check_allowed


def test_ids_are_unique_and_match_keys():
    all_sets = catalog.HAZARDS + catalog.CITY
    assert len({d.id for d in all_sets}) == len(all_sets)
    assert all(catalog.DATASETS[d.id] is d for d in all_sets)


def test_every_layer_url_is_allowlisted():
    for d in catalog.DATASETS.values():
        assert check_allowed(d.layer_url) == d.layer_url
        assert check_allowed(d.layer_url + "/query") == d.layer_url + "/query"


def test_hazards_use_hazard_buffer_and_have_disclaimers():
    for d in catalog.HAZARDS:
        assert d.category == "hazard"
        assert d.buffer == "hazard"
        assert d.disclaimer


def test_city_layers_use_city_buffer():
    for d in catalog.CITY:
        assert d.buffer == "city"
        assert d.source_agency.startswith("City of Glendale")


def test_parcels_are_live_only_and_everything_else_is_snapshot():
    assert [d.id for d in catalog.datasets(access="live")] == ["parcels"]
    assert catalog.get_dataset("parcels").access == "live"


def test_resources_have_name_fields():
    for d in catalog.datasets(category="resource"):
        assert d.name_field, d.id
        assert d.name_field in d.field_names, d.id


def test_class_fields_are_documented():
    for d in catalog.DATASETS.values():
        if d.class_field:
            assert d.class_field in d.field_names, d.id


def test_fhsz_codes_match_official_definitions():
    fhsz = {f.name: f for f in catalog.get_dataset("calfire_fhsz_lra").fields}["FHSZ"]
    assert fhsz.values["-3"] == "Unzoned, Non Wildland"
    assert fhsz.values["3"] == "Very High Fire Hazard Severity Zone"


def test_sample_world_cities_is_not_in_catalog():
    assert not any("SampleWorldCities" in d.layer_url for d in catalog.DATASETS.values())


def test_unknown_dataset_error_lists_known_ids():
    with pytest.raises(KeyError, match="calfire_fhsz_lra"):
        catalog.get_dataset("nope")
