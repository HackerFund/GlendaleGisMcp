# Real-Time Emergency Information: Where to Find It

This MCP server has **no real-time data**: no active fires, evacuation orders, weather warnings, earthquakes or outages. It covers preparedness data, meaning which hazards a place is mapped for before anything happens. This guide lists the official sources for what is happening now, for residents and for teams building apps.

*Sources checked September 18, 2026. Services change, so confirm a source before building on it.*

## Contents

- [Why the server leaves this out](#why-the-server-leaves-this-out)
- [For residents: official alerts](#for-residents-official-alerts)
- [For developers: data feeds](#for-developers-data-feeds)
- [Example: current fire perimeters](#example-current-fire-perimeters)
- [By hazard](#by-hazard)
- [Rules for using real-time data in an app](#rules-for-using-real-time-data-in-an-app)
- [Related, but not real-time: fire history](#related-but-not-real-time-fire-history)

## Why the server leaves this out

During an emergency, people act on what they're told. A hackathon server can be slow, stale or down at exactly the wrong moment, and a missed evacuation order can cost lives. So the server stays out of that path, and apps should send people to the official channels below. The same rule is in the project's scope: "Live emergency alerts or evacuation orders" are out of scope.

The hazard maps are the opposite case. They change every few months or years, so an offline snapshot of them is accurate.

## For residents: official alerts

These are the channels people should sign up for. An app can link to them; it shouldn't replace them.

| Channel | Who runs it | What it sends | Where |
| --- | --- | --- | --- |
| **Everbridge** (Glendale's mass notification system) | City of Glendale | Evacuations, inundation zones, street closures and shelters during large emergencies | [Emergency Communications](https://www.glendaleca.gov/government/departments/fire-department/other/emergency-preparedness-response/city-wide-emergency-communications) |
| **Know Your Zone** | Glendale Fire Department | Explains evacuation zone IDs, and the difference between an evacuation warning and an order | [Know Your Zone](https://www.glendaleca.gov/government/departments/fire-department/other-links/emergency-preparedness-response/know-your-zone) |
| **Nixle** | Glendale Police Department | Local police advisories | [Nixle sign-up](https://www.glendaleca.gov/Home/Components/News/News/1018) |
| **Alert LA County** | LA County Office of Emergency Management | Countywide emergency alerts by text, email and phone | [Register](https://alertlacounty.genasys.com/portal/en/register) |
| **Genasys Protect** | Used by LA County agencies | Evacuation zone map and live zone status: look up an address to find its zone | [protect.genasys.com](https://protect.genasys.com/) |
| **Wireless Emergency Alerts (WEA)** | Federal, sent by authorized agencies | Urgent alerts pushed to phones in the affected area | On by default on most phones; check the phone's settings |
| **MyShake** | UC Berkeley, using USGS ShakeAlert | Earthquake early warning, seconds before shaking | [earthquake.ca.gov/get-alerts](https://www.earthquake.ca.gov/get-alerts/) |
| **GWP outage text alerts** | Glendale Water & Power | Power outage status and restoration times | [Power Outages](https://www.glendaleca.gov/government/departments/glendale-water-and-power/safety-security/power-outages) |
| **Ready LA County** | LA County | Preparedness guides and a list of alert systems | [ready.lacounty.gov](https://ready.lacounty.gov/emergency-notifications/) |

The City also publishes an [Emergency Response Web Map Collection](https://www.glendaleca.gov/government/departments/office-of-the-city-manager/communications-community-relations/emergency-response-web-map-collection), including evacuation centers and evacuation zones.

## For developers: data feeds

These are machine-readable sources we confirmed respond. "Official" means published by the responsible agency.

| Feed | Format | Key needed | Notes |
| --- | --- | --- | --- |
| [NWS active alerts](https://www.weather.gov/documentation/services-web-api): `https://api.weather.gov/alerts/active?point=34.146,-118.255` | GeoJSON | No, but a `User-Agent` with contact info is required | Official. Red Flag Warnings, flash flood warnings (including for debris flows from burn scars), high wind, excessive heat. Filter by point, zone or state. |
| [WFIGS current interagency fire perimeters](https://data-nifc.opendata.arcgis.com/datasets/nifc::wfigs-current-interagency-fire-perimeters/about): `https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters_Current/FeatureServer/0` | ArcGIS REST (same as this server's sources) | No | Official (National Interagency Fire Center). Best available perimeters of recent and active fires; not every incident has one. Fields include `poly_IncidentName`, `poly_GISAcres`, `poly_DateCurrent`. An older URL, `Current_WildlandFire_Perimeters`, now requires a token. |
| CAL FIRE incidents: `https://incidents.fire.ca.gov/umbraco/api/IncidentApi/GeoJsonList?inactive=false` | GeoJSON (points) | No | The feed behind [CAL FIRE's incidents page](https://www.fire.ca.gov/incidents). **Undocumented**, so it could change without notice. Fields include `Name`, `AcresBurned`, `PercentContained`, `Started`, `Updated`, `Url`. |
| [USGS earthquake feeds](https://earthquake.usgs.gov/earthquakes/feed/v1.0/geojson.php), e.g. `https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_hour.geojson` | GeoJSON | No | Official. Earthquakes after they happen, updated every minute. Not early warning: that reaches the public only through MyShake, WEA and Android, not as an open feed. |
| [AirNow API](https://docs.airnowapi.org/) | JSON | Yes (free account) | Official EPA air quality, including wildfire smoke. The data is preliminary and not fully validated. |
| [National Water Prediction Service](https://water.noaa.gov/) | Web maps and data | No | Official NOAA river gauges and flood forecasts. |

Genasys Protect evacuation zones and statuses are shown on [protect.genasys.com](https://protect.genasys.com/). We did not find a documented public API for them, so link to the site rather than scraping it.

## Example: current fire perimeters

The NIFC perimeter service is small: on September 18, 2026 it held 183 perimeters nationwide, 7 of them in California. All 7 with geometry came to about 800 KB in one 0.6-second request. So the simplest approach is to fetch every California perimeter once per cache period and answer your users from that copy. Your app then makes one request per period, however many people use it.

```python
import time

import httpx

PERIMETERS = (
    "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
    "WFIGS_Interagency_Perimeters_Current/FeatureServer/0/query"
)
TTL_S = 30 * 60  # refetch at most every 30 minutes
_cache = {"at": 0.0, "data": None}


def california_perimeters():
    """Current fire perimeters in California, fetched at most once per TTL_S."""
    if _cache["data"] is None or time.time() - _cache["at"] > TTL_S:
        response = httpx.get(
            PERIMETERS,
            params={
                "where": "attr_POOState='US-CA'",
                "outFields": "poly_IncidentName,poly_GISAcres,poly_DateCurrent,"
                "attr_PercentContained",
                "outSR": 4326,
                "f": "geojson",
            },
            headers={"User-Agent": "MyGlendaleApp (you@example.com)"},
            timeout=60,
        )
        response.raise_for_status()
        _cache.update(at=time.time(), data=response.json())
    return _cache["data"], _cache["at"]


perimeters, fetched_at = california_perimeters()
for fire in perimeters["features"]:
    p = fire["properties"]
    print(p["poly_IncidentName"], round(p["poly_GISAcres"] or 0), "acres")
```

Notes:

- **Cache for 15–30 minutes.** Perimeters themselves usually update only a few times a day per fire, often from overnight mapping flights, so a longer cache adds delay on top of that. Even a 15-minute cache is only 96 requests a day.
- **Show two times:** when the perimeter was drawn (`poly_DateCurrent`, in milliseconds since 1970) and when you fetched it.
- **One fire can have several perimeter features.** Group by incident if you list fires.
- **Many fires have no perimeter yet,** especially new, fast ones. An empty result means "no mapped perimeters in this feed", never "no fire nearby". Always link to [CAL FIRE incidents](https://www.fire.ca.gov/incidents) and [Genasys Protect](https://protect.genasys.com/).
- To measure distance to a perimeter, load it with shapely and use `glendale_gis.core.geo.distance_m`, as shown in [Reading the Snapshot Data](snapshot-data.md#using-the-data).

## By hazard

| Hazard | Mapped hazard (this server) | Happening now (official sources) |
| --- | --- | --- |
| Wildfire | `wildfire_zone` (CAL FIRE hazard severity zones) | CAL FIRE incidents, WFIGS perimeters, NWS Red Flag Warnings, AirNow smoke; evacuations via Everbridge, Alert LA County and Genasys Protect |
| Earthquake | `seismic_zones` (fault rupture, liquefaction, landslide zones) | MyShake and WEA for early warning; USGS feeds after the fact |
| Flood and debris flow | `flood_zone`, `debris_flow` | NWS flash flood warnings (they cover burn-scar debris flows); NOAA river gauges |
| Dam failure | `dam_inundation` | No public real-time dam status feed that we found. A dam emergency would reach residents as an evacuation order through the alert systems above. |
| Power outage | Not covered | GWP outage map and text alerts |

Watch Duty ([watchduty.org](https://www.watchduty.org/)) is a popular nonprofit wildfire app that relays scanner traffic and agency updates. It is useful, but it is not an official source.

## Rules for using real-time data in an app

- **Link to the official source, and put it first.** Show where each item came from, and send people to the agency for decisions.
- **Always show the time.** Show when the data was published and when your app last fetched it. Stale data should look stale.
- **Fail visibly.** If a feed can't be reached, say so. Never show an empty list as "no alerts".
- **Never imply an all-clear.** No active incident in a feed doesn't mean an area is safe, and feeds lag reality.
- **Don't rebroadcast evacuation orders as your own.** Show the agency's message and link to it, word for word.
- **Be a good neighbor:** poll no more often than the feed updates, cache responses, and send a descriptive `User-Agent` with contact information (the NWS API requires one).
- **Combine carefully with the hazard maps.** "In a Very High fire hazard zone" and "a fire is burning nearby" are different facts from different sources. Label each one.

## Related, but not real-time: fire history

CAL FIRE's Fire and Resource Assessment Program (FRAP) publishes historical fire perimeters back to 1878, updated once a year in spring. They show where fires have burned, not where they are burning. The dataset is incomplete, especially for older fires.

- [California Historical Fire Perimeters](https://data.ca.gov/dataset/california-historical-fire-perimeters) on the California Open Data Portal
- [Fire perimeters](https://www.fire.ca.gov/what-we-do/fire-resource-assessment-program/fire-perimeters) at CAL FIRE

This dataset isn't in the server's catalog, but it could be added as a snapshot layer, like the hazard zones.
