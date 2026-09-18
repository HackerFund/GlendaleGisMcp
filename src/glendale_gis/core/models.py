"""Typed inputs and outputs shared by the core lookups and the MCP tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

HazardStatus = Literal["in_zone", "not_in_zone", "unavailable"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------------
# Location
# --------------------------------------------------------------------------------------------


class Location(_Model):
    """Where to look: a street address in Glendale, or a latitude/longitude.

    Give either ``address`` or both ``lat`` and ``lon``. Coordinates skip geocoding.
    """

    address: str | None = Field(
        default=None, min_length=1, max_length=200, description="Street address in Glendale, CA"
    )
    lat: float | None = Field(default=None, ge=-90, le=90, description="Latitude (WGS 84)")
    lon: float | None = Field(default=None, ge=-180, le=180, description="Longitude (WGS 84)")

    @model_validator(mode="after")
    def _one_kind(self) -> Location:
        has_coords = self.lat is not None or self.lon is not None
        if self.address is not None and has_coords:
            raise ValueError("Give either an address or lat/lon, not both")
        if self.address is None and (self.lat is None or self.lon is None):
            raise ValueError("Give an address, or both lat and lon")
        return self


class ResolvedLocation(_Model):
    """The point a lookup used, and how it was found."""

    lat: float
    lon: float
    in_city: bool = Field(description="Whether the point is inside the Glendale city boundary")
    source: Literal["coordinates", "geocoder"]
    matched_address: str | None = None
    score: float | None = Field(default=None, description="Geocoder match score (0-100)")


# --------------------------------------------------------------------------------------------
# Shared pieces
# --------------------------------------------------------------------------------------------


class Ref(_Model):
    """Identifies a feature, so the full live record can be fetched from ``layer_url``."""

    dataset: str
    object_id: int | None
    global_id: str | None = Field(description="Null for layers without a GlobalID field")
    layer_url: str


class Meta(_Model):
    source: str = Field(description="Publishing agency")
    url: str = Field(description="Source layer URL")
    cached: bool = Field(description="True when served from the snapshot or cache")
    as_of: str | None = Field(description="When the data was fetched from the source (UTC)")
    stale: bool = Field(description="True when fresher data could not be fetched")
    source_last_edit: str | None = Field(
        default=None, description="When the source says it last changed, if it publishes that"
    )


class Feature(_Model):
    attributes: dict[str, Any] = Field(description="Source attributes, unchanged")
    ref: Ref


class Nearest(Feature):
    distance_m: int = Field(description="Straight-line distance in meters; 0 when inside")
    exact: bool = Field(
        description=(
            "False when a closer feature could exist outside the area the snapshot covers "
            "(the distance is an upper bound)"
        )
    )


# --------------------------------------------------------------------------------------------
# Hazards
# --------------------------------------------------------------------------------------------


class HazardResult(_Model):
    location: ResolvedLocation | None = Field(
        default=None, description="Set when the result is returned on its own"
    )
    dataset: str
    title: str
    status: HazardStatus
    reason: str | None = Field(default=None, description="Why the result is unavailable")
    matches: list[Feature] = Field(
        default_factory=list,
        description=(
            "Every feature that contains the point, including classes the source defines as "
            "unzoned (e.g. wildfire NonWildland), which don't count toward in_zone"
        ),
    )
    nearest: Nearest | None = Field(
        default=None, description="Nearest zone feature (0 m when inside); unclassed layers only"
    )
    nearest_by_class: dict[str, Nearest] | None = Field(
        default=None,
        description="Nearest zone feature for each class, for classed layers (wildfire, flood)",
    )
    class_field: str | None = Field(default=None, description="Attribute the classes come from")
    notes: list[str] = Field(default_factory=list)
    disclaimer: str
    meta: Meta | None = Field(default=None, alias="_meta", serialization_alias="_meta")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class SeismicZones(_Model):
    location: ResolvedLocation
    fault: HazardResult
    liquefaction: HazardResult
    landslide: HazardResult


class HazardsAtLocation(_Model):
    location: ResolvedLocation
    wildfire: HazardResult
    flood: HazardResult
    fault: HazardResult
    liquefaction: HazardResult
    landslide: HazardResult
    dam_inundation: HazardResult
    debris_flow: HazardResult


# --------------------------------------------------------------------------------------------
# Resources
# --------------------------------------------------------------------------------------------


class Resource(Feature):
    kind: str = Field(description="Dataset id, e.g. fire_stations")
    name: str | None
    address: str | None
    distance_m: int = Field(description="Straight-line distance in meters; 0 when inside a park")


class ResourceGroup(_Model):
    kind: str
    title: str
    status: Literal["ok", "unavailable"]
    reason: str | None = None
    results: list[Resource] = Field(default_factory=list)
    total_in_dataset: int = Field(default=0, description="Features of this kind in the snapshot")
    meta: Meta | None = Field(default=None, alias="_meta", serialization_alias="_meta")

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class NearestResources(_Model):
    location: ResolvedLocation
    groups: list[ResourceGroup]
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------


class ToolError(_Model):
    """An error a caller can act on: what went wrong and what to try next."""

    error: str
    suggestions: list[str] = Field(default_factory=list)
    candidates: list[dict[str, Any]] | None = None
