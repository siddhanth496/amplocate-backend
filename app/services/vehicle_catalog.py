"""Built-in catalog of common Indian EVs (MVP: static; later served from DB)."""
from typing import Optional

CATALOG = [
    # 4W
    {"id": "tata-nexon-ev-lr", "make": "Tata", "model": "Nexon EV Long Range", "category": "4W",
     "battery_kwh": 40.5, "efficiency_wh_per_km": 145, "connector_types": ["CCS2", "Type2_AC"], "max_dc_power_kw": 50},
    {"id": "tata-tiago-ev", "make": "Tata", "model": "Tiago EV", "category": "4W",
     "battery_kwh": 24.0, "efficiency_wh_per_km": 110, "connector_types": ["CCS2", "Type2_AC"], "max_dc_power_kw": 25},
    {"id": "tata-punch-ev", "make": "Tata", "model": "Punch EV", "category": "4W",
     "battery_kwh": 35.0, "efficiency_wh_per_km": 125, "connector_types": ["CCS2", "Type2_AC"], "max_dc_power_kw": 50},
    {"id": "mg-zs-ev", "make": "MG", "model": "ZS EV", "category": "4W",
     "battery_kwh": 50.3, "efficiency_wh_per_km": 155, "connector_types": ["CCS2", "Type2_AC"], "max_dc_power_kw": 76},
    {"id": "mg-comet-ev", "make": "MG", "model": "Comet EV", "category": "4W",
     "battery_kwh": 17.3, "efficiency_wh_per_km": 90, "connector_types": ["Type2_AC", "Wall_3pin"], "max_dc_power_kw": 0},
    {"id": "mahindra-xuv400", "make": "Mahindra", "model": "XUV400", "category": "4W",
     "battery_kwh": 39.4, "efficiency_wh_per_km": 140, "connector_types": ["CCS2", "Type2_AC"], "max_dc_power_kw": 50},
    {"id": "hyundai-creta-ev", "make": "Hyundai", "model": "Creta Electric", "category": "4W",
     "battery_kwh": 51.4, "efficiency_wh_per_km": 145, "connector_types": ["CCS2", "Type2_AC"], "max_dc_power_kw": 100},
    {"id": "byd-atto3", "make": "BYD", "model": "Atto 3", "category": "4W",
     "battery_kwh": 60.5, "efficiency_wh_per_km": 150, "connector_types": ["CCS2", "Type2_AC"], "max_dc_power_kw": 80},
    # 2W
    {"id": "ather-450x", "make": "Ather", "model": "450X", "category": "2W",
     "battery_kwh": 3.7, "efficiency_wh_per_km": 28, "connector_types": ["Bharat_AC001", "Wall_3pin"], "max_dc_power_kw": 0},
    {"id": "ola-s1-pro", "make": "Ola", "model": "S1 Pro", "category": "2W",
     "battery_kwh": 4.0, "efficiency_wh_per_km": 27, "connector_types": ["Wall_3pin"], "max_dc_power_kw": 0},
    {"id": "tvs-iqube", "make": "TVS", "model": "iQube", "category": "2W",
     "battery_kwh": 3.4, "efficiency_wh_per_km": 30, "connector_types": ["Wall_3pin"], "max_dc_power_kw": 0},
    {"id": "bajaj-chetak", "make": "Bajaj", "model": "Chetak", "category": "2W",
     "battery_kwh": 3.2, "efficiency_wh_per_km": 29, "connector_types": ["Wall_3pin"], "max_dc_power_kw": 0},
]


def get_entry(catalog_id: str) -> Optional[dict]:
    return next((e for e in CATALOG if e["id"] == catalog_id), None)
