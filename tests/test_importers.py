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
