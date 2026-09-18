"""Address lookup with the City of Glendale geocoder.

The city's locator also matches addresses in Burbank and unincorporated La Crescenta (scores up to
100), and its ``City`` field isn't reliable, so every candidate is checked against the city
boundary. It ignores unit numbers and has no place-name search. Rules and thresholds come from the
Phase 0 checks in ``Plans/city-sources.md``.

Addresses are never logged. Cache keys are hashed (see ``core.cache``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from shapely.geometry import Point

from glendale_gis.core import geo
from glendale_gis.core.arcgis import ArcGISClient, ArcGISError
from glendale_gis.core.cache import Cache, get_or_fetch
from glendale_gis.core.catalog import GEOCODER
from glendale_gis.core.config import Settings
from glendale_gis.core.hazards import locate
from glendale_gis.core.models import (
    ActionableError,
    GeocodeCandidate,
    GeocodeResult,
    Location,
    Meta,
    ResolvedLocation,
)
from glendale_gis.core.snapshot import Snapshot

CACHE_NAMESPACE = "geocode"
MAX_LOCATIONS = 10
MAX_CANDIDATES_RETURNED = 5
MIN_CONFIDENT_SCORE = 90.0
LEAD_MARGIN = 5.0  # a PointAddress this far ahead of the next candidate wins a tie-break
DUPLICATE_M = 50.0  # PointAddress/StreetAddress pairs for one address sit ~40 m apart
BOUNDARY_TOLERANCE_M = 25.0  # addresses on the city line still count

# Intersections are precise points; street-only matches (no house number) never are.
CONFIDENT_TYPES = frozenset({"PointAddress", "StreetAddress", "StreetInt"})
TYPE_RANK = {"PointAddress": 0, "StreetAddress": 1, "StreetInt": 2, "StreetName": 3}

UNIT_RE = re.compile(
    r"(?:\b(?:apt|apartment|unit|suite|ste|spc|space|rm|room)\b\.?\s*[\w-]+|#\s*[\w-]+)",
    re.IGNORECASE,
)

TRY_COORDINATES = "Give the location as lat/lon instead; coordinates skip the geocoder."


@dataclass(frozen=True)
class _Candidate:
    address: str
    score: float
    match_type: str
    lat: float
    lon: float
    local: Point  # local meters
    in_city: bool

    def to_model(self) -> GeocodeCandidate:
        return GeocodeCandidate(
            address=self.address,
            score=self.score,
            match_type=self.match_type,
            lat=self.lat,
            lon=self.lon,
            in_city=self.in_city,
        )


class Geocoder:
    def __init__(
        self, client: ArcGISClient, cache: Cache, snapshot: Snapshot, settings: Settings
    ) -> None:
        self._client = client
        self._cache = cache
        self._snapshot = snapshot
        self._ttl_s = settings.geocode_cache_ttl_s

    # -- public ------------------------------------------------------------------------------

    async def geocode(self, address: str) -> GeocodeResult:
        """Match an address. Raises ``ActionableError`` if nothing in Glendale matches."""
        text = " ".join(address.split())
        if not text:
            raise ActionableError("The address is empty.", ["Give a street address in Glendale."])
        try:
            fetched = await get_or_fetch(
                self._cache,
                CACHE_NAMESPACE,
                text.lower(),
                self._ttl_s,
                lambda: self._fetch(text),
                retryable=(ArcGISError,),
            )
        except ArcGISError:
            raise ActionableError(
                "The City of Glendale geocoder isn't responding right now.",
                ["Try again in a minute.", TRY_COORDINATES],
            ) from None

        meta = Meta(
            source="City of Glendale",
            url=GEOCODER.url,
            cached=fetched.cached,
            as_of=_iso(fetched.fetched_at),
            stale=fetched.stale,
        )
        return self._decide(text, [self._candidate(raw) for raw in fetched.value], meta)

    async def resolve(self, location: Location) -> ResolvedLocation:
        """Turn a ``Location`` into a point: coordinates as given, or one confident match.

        Raises ``ActionableError`` for ambiguous, unknown or out-of-city addresses.
        """
        if location.address is None:
            assert location.lat is not None and location.lon is not None
            return locate(self._snapshot, location.lat, location.lon)
        result = await self.geocode(location.address)
        if result.location is not None:
            return result.location
        candidates = [c.model_dump() for c in result.candidates]
        if all(c.match_type == "StreetName" for c in result.candidates):
            raise ActionableError(
                "Only the street matched, not a specific address.",
                [
                    "Check the house number.",
                    "Place names aren't supported; use a street address.",
                    TRY_COORDINATES,
                ],
                candidates=candidates,
            )
        raise ActionableError(
            "The address matches more than one place in Glendale.",
            [
                "Pick one of the candidates and pass its lat/lon.",
                "Or add the street direction (N, S, E, W) or ZIP code to the address.",
            ],
            candidates=candidates,
        )

    # -- internals ---------------------------------------------------------------------------

    async def _fetch(self, text: str) -> list[dict[str, Any]]:
        body = await self._client.request_json(
            f"{GEOCODER.url}/findAddressCandidates",
            {
                "SingleLine": text,
                "outFields": "Match_addr,Addr_type,Score",
                "maxLocations": MAX_LOCATIONS,
                "outSR": 4326,
            },
        )
        found = []
        for c in body.get("candidates") or []:
            # X/Y attributes stay in state-plane feet; only `location` is reprojected.
            loc = c.get("location") or {}
            if loc.get("x") is None or loc.get("y") is None:
                continue
            attrs = c.get("attributes") or {}
            found.append(
                {
                    "address": str(c.get("address") or attrs.get("Match_addr") or ""),
                    "score": float(c.get("score") or attrs.get("Score") or 0),
                    "match_type": str(attrs.get("Addr_type") or ""),
                    "lon": float(loc["x"]),
                    "lat": float(loc["y"]),
                }
            )
        return found

    def _candidate(self, raw: dict[str, Any]) -> _Candidate:
        local = geo.to_local(Point(raw["lon"], raw["lat"]))
        return _Candidate(
            address=raw["address"],
            score=raw["score"],
            match_type=raw["match_type"],
            lat=raw["lat"],
            lon=raw["lon"],
            local=local,
            in_city=self._snapshot.in_city(local),
        )

    def _decide(self, text: str, found: list[_Candidate], meta: Meta) -> GeocodeResult:
        if not found:
            raise ActionableError(
                "No address in Glendale matched.",
                [
                    "Check the spelling and include the house number and street, e.g. "
                    "'613 E Broadway'.",
                    "Place names (e.g. 'Glendale Galleria') aren't supported; use a street "
                    "address.",
                    TRY_COORDINATES,
                ],
            )
        boundary = self._snapshot.boundary
        inside = [c for c in found if boundary.distance(c.local) <= BOUNDARY_TOLERANCE_M]
        if not inside:
            raise ActionableError(
                "The address is outside the Glendale city limits. The city's geocoder also "
                "covers neighboring areas such as Burbank and unincorporated La Crescenta.",
                [
                    "Check the address is in Glendale, CA.",
                    "Hazard tools also cover up to 2 km outside Glendale if you give lat/lon.",
                ],
                candidates=[c.to_model().model_dump() for c in _best_first(found)],
            )

        kept = _collapse_duplicates(inside)
        top = kept[0]
        confident = (
            top.match_type in CONFIDENT_TYPES
            and top.score >= MIN_CONFIDENT_SCORE
            and (
                len(kept) == 1
                or (top.match_type == "PointAddress" and top.score >= kept[1].score + LEAD_MARGIN)
            )
        )

        notes = []
        if UNIT_RE.search(text):
            notes.append(
                "The unit or apartment number was ignored; the city geocoder matches buildings, "
                "not units."
            )
        dropped = len(found) - len(inside)
        if dropped:
            notes.append(f"{dropped} match(es) outside the Glendale city limits were left out.")
        if not confident and top.match_type == "StreetName":
            notes.append("Only the street matched, not the house number.")

        location = None
        if confident:
            location = ResolvedLocation(
                lat=top.lat,
                lon=top.lon,
                in_city=top.in_city,
                source="geocoder",
                matched_address=top.address,
                score=top.score,
            )
        return GeocodeResult(
            status="matched" if confident else "ambiguous",
            location=location,
            candidates=[c.to_model() for c in kept[:MAX_CANDIDATES_RETURNED]],
            notes=notes,
            _meta=meta,
        )


def _best_first(candidates: list[_Candidate]) -> list[_Candidate]:
    return sorted(candidates, key=lambda c: (-c.score, TYPE_RANK.get(c.match_type, 9)))


def _collapse_duplicates(candidates: list[_Candidate]) -> list[_Candidate]:
    """Drop candidates within ``DUPLICATE_M`` of a better one, preferring PointAddress."""
    by_quality = sorted(candidates, key=lambda c: (TYPE_RANK.get(c.match_type, 9), -c.score))
    kept: list[_Candidate] = []
    for c in by_quality:
        if all(c.local.distance(k.local) > DUPLICATE_M for k in kept):
            kept.append(c)
    return _best_first(kept)


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(microsecond=0).isoformat()
