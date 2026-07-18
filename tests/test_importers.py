"""Unit tests for the OCM / Overpass importers (mapping + dedupe logic)."""
import pytest

from app.seed.ocm_import import to_charger as ocm_to_charger
from app.seed.overpass_import import to_charger as osm_to_charger
from app.seed.regions import bbox_around, NCR_REGIONS

pytestmark = pytest.mark.asyncio


def test_ocm_mapping():
    poi = {
        "ID": 12345,
        "AddressInfo": {
            "Title": "Tata Power - CP", "AddressLine1": "Block K", "Town": "New Delhi",
            "Latitude": 28.6315, "Longitude": 77.2196,
        },
        "OperatorInfo": {"Title": "Tata Power"},
        "Connections": [
            {"ConnectionTypeID": 33, "PowerKW": 50, "Quantity": 2},   # CCS2
            {"ConnectionTypeID": 25, "PowerKW": 22, "Quantity": 1},   # Type2
            {"ConnectionTypeID": 9999, "PowerKW": 11, "Quantity": 1}, # unknown → dropped
        ],
    }
    c = ocm_to_charger(poi)
    assert c.external_id == "ocm-12345"
    assert c.operator == "Tata Power"
    assert {x["type"] for x in c.connectors} == {"CCS2", "Type2_AC"}
    assert c.lat == 28.6315


def test_ocm_skips_unmappable():
    assert ocm_to_charger({"ID": 1, "AddressInfo": {}}) is None
    assert ocm_to_charger({
        "ID": 2,
        "AddressInfo": {"Latitude": 28.6, "Longitude": 77.2},
        "Connections": [{"ConnectionTypeID": 9999}],
    }) is None


def test_osm_node_mapping():
    el = {
        "type": "node", "id": 987, "lat": 28.55, "lon": 77.30,
        "tags": {
            "amenity": "charging_station",
            "name": "Statiq Hub",
            "operator": "Statiq",
            "socket:type2": "2",
            "socket:type2:output": "22 kW",
            "socket:ccs": "1",
            "addr:city": "Noida",
        },
    }
    c = osm_to_charger(el)
    assert c.external_id == "osm-node-987"
    assert c.city == "Noida"
    types = {x["type"]: x for x in c.connectors}
    assert types["Type2_AC"]["count"] == 2
    assert types["Type2_AC"]["power_kw"] == 22
    assert "CCS2" in types


def test_osm_way_uses_center_and_fallback_connector():
    el = {"type": "way", "id": 55, "center": {"lat": 28.4, "lon": 77.1}, "tags": {"amenity": "charging_station"}}
    c = osm_to_charger(el)
    assert c.lat == 28.4
    assert c.connectors == [{"type": "Type2_AC", "power_kw": 7.4, "count": 1}]


def test_bbox_around():
    south, west, north, east = bbox_around(28.6139, 77.2090, 18)
    assert south < 28.6139 < north
    assert west < 77.2090 < east
    assert 0.3 < (north - south) < 0.4  # ~36 km tall


def test_ncr_grid_covers_major_cities():
    names = " ".join(r[0] for r in NCR_REGIONS)
    for city in ("Delhi", "Gurugram", "Noida", "Ghaziabad", "Faridabad"):
        assert city in names


def test_google_places_mapping():
    from app.seed.google_places_import import to_charger
    place = {
        "id": "ChIJabc123",
        "displayName": {"text": "Tata Power Charging Station"},
        "formattedAddress": "Connaught Place, New Delhi",
        "location": {"latitude": 28.6315, "longitude": 77.2196},
        "businessStatus": "OPERATIONAL",
        "evChargeOptions": {
            "connectorCount": 4,
            "connectorAggregation": [
                {"type": "EV_CONNECTOR_TYPE_CCS_COMBO_2", "maxChargeRateKw": 60, "count": 2},
                {"type": "EV_CONNECTOR_TYPE_TYPE_2", "maxChargeRateKw": 22, "count": 2},
                {"type": "EV_CONNECTOR_TYPE_TESLA", "maxChargeRateKw": 250, "count": 1},  # unmapped → dropped
            ],
        },
    }
    c = to_charger(place)
    assert c.external_id == "gplace-ChIJabc123"
    types = {x["type"]: x for x in c.connectors}
    assert types["CCS2"]["power_kw"] == 60 and types["CCS2"]["count"] == 2
    assert "Type2_AC" in types and "TESLA" not in str(types)


def test_google_places_skips_closed_and_defaults_connectors():
    from app.seed.google_places_import import to_charger
    assert to_charger({
        "id": "x", "location": {"latitude": 1, "longitude": 2},
        "businessStatus": "CLOSED_PERMANENTLY",
    }) is None
    c = to_charger({"id": "y", "location": {"latitude": 28.5, "longitude": 77.1}})
    assert c.connectors == [{"type": "Type2_AC", "power_kw": 7.4, "count": 1}]


def test_google_tile_centers():
    from app.seed.google_places_import import tile_centers
    small = tile_centers(28.6, 77.2, 8)
    assert small == [(28.6, 77.2, 8)]
    big = tile_centers(28.6, 77.2, 25)
    assert 2 <= len(big) <= 15
    assert all(r <= 10 for _, _, r in big)


# ── Statiq importer ───────────────────────────────────────────────────────────
def test_statiq_connector_mapping():
    from app.seed.statiq_import import map_connector
    assert map_connector("CCS-2") == "CCS2"
    assert map_connector("Type 2") == "Type2_AC"
    assert map_connector("CHAdeMO") == "CHAdeMO"
    assert map_connector("GB/T") == "GB/T"
    assert map_connector("Wall") == "Wall_3pin"
    assert map_connector("Smart Plug 881") == "Wall_3pin"
    assert map_connector("Tesla NACS") is None
    assert map_connector(None) is None


def test_statiq_num_parsing():
    from app.seed.statiq_import import _num
    assert _num("₹ 24.15") == 24.15
    assert _num("120 kW") == 120.0
    assert _num(22.99) == 22.99
    assert _num(None) is None


def test_statiq_to_charger_aggregates_and_prices():
    from app.seed.statiq_import import to_charger
    station = {
        "id": "4741", "name": "Monk Mansion", "operator": "Statiq",
        "address": "Electronic City", "city": "Bengaluru",
        "lat": 12.8452, "lng": 77.6602, "available": True,
        "amenities": ["Restroom", "Cafe"],
        "chargers": [
            {"current": "DC", "power_kw": 120, "price": 24.15,
             "connectors": [{"type": "CCS-2", "status": "charging"},
                            {"type": "CCS-2", "status": "available"}]},
            {"current": "DC", "power_kw": 120, "price": 22.99,
             "connectors": [{"type": "CCS-2", "status": "available"},
                            {"type": "CCS-2", "status": "available"}]},
            {"current": "AC", "power_kw": 3.3, "price": 22.99,
             "connectors": [{"type": "Wall", "status": "available"}]},
        ],
    }
    c = to_charger(station)
    assert c.external_id == "statiq-4741"
    assert c.operator == "Statiq"
    types = {x["type"]: x for x in c.connectors}
    assert types["CCS2"]["count"] == 4 and types["CCS2"]["power_kw"] == 120
    assert types["Wall_3pin"]["count"] == 1
    assert c.price_per_kwh == 22.99          # min across chargers
    assert c.status == "online"              # available connectors present
    assert c.amenities == ["restroom", "cafe"]


def test_statiq_to_charger_offline_and_defaults():
    from app.seed.statiq_import import to_charger
    # explicitly unavailable, no connectors → offline + fallback connector
    c = to_charger({"id": "9", "lat": 1.0, "lng": 2.0, "available": False, "chargers": []})
    assert c.status == "offline"
    assert c.connectors == [{"type": "Type2_AC", "power_kw": 7.4, "count": 1}]
    # missing coordinates → unimportable
    assert to_charger({"id": "9", "chargers": []}) is None


def test_statiq_parse_next_data():
    import json
    from app.seed.statiq_import import parse_station_page
    payload = {"props": {"pageProps": {"station": {
        "id": 4741, "name": "Monk Mansion", "city": "Bengaluru",
        "address": "Electronic City", "latitude": 12.8452, "longitude": 77.6602,
        "amenities": [{"name": "Cafe"}],
        "chargers": [{"currentType": "DC", "power": "120 kW", "price": "₹ 24.15",
                      "connectors": [{"connectorType": "CCS-2", "status": "available"}]}],
    }}}}
    html = f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script></body></html>'
    norm = parse_station_page(html, "https://www.statiq.in/x-ev-charging-station-id-4741")
    assert norm["id"] == "4741"
    assert norm["city"] == "Bengaluru"
    assert round(norm["lat"], 4) == 12.8452
    assert norm["chargers"][0]["power_kw"] == 120.0
    assert norm["chargers"][0]["connectors"][0]["type"] == "CCS-2"


def test_statiq_maps_link_coord_fallback():
    import json
    from app.seed.statiq_import import parse_station_page
    # station node lacks coords; they must be recovered from the directions link
    payload = {"props": {"pageProps": {"station": {
        "id": 55, "name": "No-Geo Station", "chargers": [],
    }}}}
    html = (
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        '<a href="https://www.google.com/maps/dir/?api=1&destination=19.076,72.8777">Get directions</a>'
    )
    norm = parse_station_page(html, "https://www.statiq.in/x-ev-charging-station-id-55")
    assert norm is not None
    assert norm["lat"] == 19.076 and norm["lng"] == 72.8777
