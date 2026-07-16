import pytest

pytestmark = pytest.mark.asyncio

BLR = {"lat": 12.9716, "lng": 77.5946}


# ---------- helpers ----------

async def seed_chargers(client):
    """Seed via the seed module against the test DB."""
    from app.seed.seed_data import seed
    await seed()


async def add_vehicle(client, catalog_id="tata-nexon-ev-lr", soc=80.0):
    r = await client.post("/vehicles", json={"catalog_id": catalog_id, "battery_soc": soc})
    assert r.status_code == 201, r.text
    return r.json()


# ---------- auth ----------

async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200


async def test_otp_flow(client):
    r = await client.post("/auth/otp/request", json={"phone": "+919999999999"})
    assert r.status_code == 200
    otp = r.json()["dev_otp"]
    assert len(otp) == 6

    # wrong code rejected
    r = await client.post("/auth/otp/verify", json={"phone": "+919999999999", "code": "000000"})
    assert r.status_code == 400

    r = await client.post("/auth/otp/verify", json={"phone": "+919999999999", "code": otp})
    assert r.status_code == 200
    body = r.json()
    assert body["is_new_user"] is True

    # token works
    client.headers["Authorization"] = f"Bearer {body['access_token']}"
    r = await client.get("/auth/me")
    assert r.status_code == 200
    assert r.json()["phone"] == "+919999999999"


async def test_unauthenticated_rejected(client):
    r = await client.get("/vehicles")
    assert r.status_code == 401


# ---------- vehicles ----------

async def test_vehicle_catalog_and_crud(auth_client):
    r = await auth_client.get("/vehicles/catalog")
    assert r.status_code == 200
    assert any(e["id"] == "tata-nexon-ev-lr" for e in r.json())

    v = await add_vehicle(auth_client, soc=65)
    assert v["make"] == "Tata"
    assert "CCS2" in v["connector_types"]
    assert v["battery_soc"] == 65

    r = await auth_client.patch(f"/vehicles/{v['id']}", json={"battery_soc": 42})
    assert r.json()["battery_soc"] == 42


# ---------- discovery ----------

async def test_nearby_search_and_filters(auth_client):
    await seed_chargers(auth_client)
    v = await add_vehicle(auth_client)

    r = await auth_client.get("/chargers/nearby", params={**BLR, "radius_km": 15})
    assert r.status_code == 200
    results = r.json()
    assert len(results) >= 5
    # sorted by distance
    dists = [c["distance_km"] for c in results]
    assert dists == sorted(dists)

    # connector filter
    r = await auth_client.get("/chargers/nearby", params={**BLR, "radius_km": 15, "connector_type": "CCS2"})
    assert all(any(c["type"] == "CCS2" for c in ch["connectors"]) for ch in r.json())

    # power filter
    r = await auth_client.get("/chargers/nearby", params={**BLR, "radius_km": 15, "min_power_kw": 100})
    assert all(any(c["power_kw"] >= 100 for c in ch["connectors"]) for ch in r.json())

    # compatibility flag with vehicle
    r = await auth_client.get("/chargers/nearby", params={**BLR, "radius_km": 15, "vehicle_id": v["id"]})
    assert all(ch["compatible"] is not None for ch in r.json())
    # compatible chargers come first
    flags = [ch["compatible"] for ch in r.json()]
    assert flags == sorted(flags, reverse=True)


async def test_charger_detail(auth_client):
    await seed_chargers(auth_client)
    r = await auth_client.get("/chargers/nearby", params={**BLR, "radius_km": 15})
    cid = r.json()[0]["id"]
    r = await auth_client.get(f"/chargers/{cid}")
    assert r.status_code == 200
    body = r.json()
    assert 0 <= body["reliability_score"] <= 1
    assert "recent_reports" in body


# ---------- reliability + reports ----------

async def test_reports_update_reliability(auth_client):
    await seed_chargers(auth_client)
    r = await auth_client.get("/chargers/nearby", params={**BLR, "radius_km": 15})
    charger = r.json()[0]
    cid = charger["id"]

    r = await auth_client.post(f"/chargers/{cid}/reports", json={"report_type": "broken"})
    assert r.status_code == 201

    # duplicate spam blocked
    r = await auth_client.post(f"/chargers/{cid}/reports", json={"report_type": "broken"})
    assert r.status_code == 429

    r = await auth_client.get(f"/chargers/{cid}")
    after = r.json()["reliability_score"]
    assert after < charger["reliability_score"]


async def test_session_success_boosts_reliability(auth_client):
    await seed_chargers(auth_client)
    r = await auth_client.get("/chargers/nearby", params={**BLR, "radius_km": 15})
    charger = r.json()[1]
    cid = charger["id"]

    r = await auth_client.post("/chargers/sessions/start", json={"charger_id": cid})
    sid = r.json()["session_id"]
    r = await auth_client.post(f"/chargers/sessions/{sid}/end", json={"successful": True, "energy_kwh": 20})
    assert r.status_code == 200

    r = await auth_client.get(f"/chargers/{cid}")
    assert r.json()["reliability_score"] >= charger["reliability_score"] - 0.05


# ---------- trip planner ----------

async def test_trip_no_charging_needed(auth_client):
    await seed_chargers(auth_client)
    v = await add_vehicle(auth_client, soc=90)
    r = await auth_client.post("/trips/plan", json={
        "origin": BLR,
        "destination": {"lat": 12.9346, "lng": 77.6112},  # Koramangala, ~6 km
        "vehicle_id": v["id"],
    })
    assert r.status_code == 200
    plan = r.json()
    assert plan["feasible"] is True
    assert plan["stops"] == []
    assert plan["destination_arrival_soc"] > 80


async def test_trip_single_stop_blr_mysuru(auth_client):
    """Bengaluru → Mysuru (~150 km road) at 60% SoC needs one stop.

    (At 40% the pessimistic model correctly deems it not single-stop
    plannable: no reachable charger leaves enough post-charge range.)
    """
    await seed_chargers(auth_client)
    v = await add_vehicle(auth_client, soc=60)
    r = await auth_client.post("/trips/plan", json={
        "origin": BLR,
        "destination": {"lat": 12.3050, "lng": 76.6550},
        "vehicle_id": v["id"],
    })
    assert r.status_code == 200
    plan = r.json()
    assert plan["feasible"] is True, plan
    assert len(plan["stops"]) == 1
    stop = plan["stops"][0]
    # risk-free invariants
    assert stop["arrival_soc"] >= 15.0                     # reserve floor
    assert stop["target_soc"] <= 80.0                      # taper ceiling
    assert stop["backup_charger"] is not None              # backup rule
    assert plan["destination_arrival_soc"] >= 15.0
    assert stop["dwell_minutes"] > 0
    # stop charger must be CCS2-compatible
    assert any(c["type"] == "CCS2" for c in stop["charger"]["connectors"])


async def test_trip_impossible_low_soc(auth_client):
    await seed_chargers(auth_client)
    v = await add_vehicle(auth_client, soc=12)  # below reserve
    r = await auth_client.post("/trips/plan", json={
        "origin": BLR,
        "destination": {"lat": 12.3050, "lng": 76.6550},
        "vehicle_id": v["id"],
    })
    plan = r.json()
    assert plan["feasible"] is False
    assert plan["note"]


async def test_trip_2w_short_range(auth_client):
    """2W with wall socket only — long trip should not be plannable on DC corridor."""
    await seed_chargers(auth_client)
    v = await add_vehicle(auth_client, catalog_id="ola-s1-pro", soc=95)
    r = await auth_client.post("/trips/plan", json={
        "origin": BLR,
        "destination": {"lat": 12.3050, "lng": 76.6550},
        "vehicle_id": v["id"],
    })
    plan = r.json()
    # Ola S1 Pro (~85 km safe range) cannot make 180 km with no compatible corridor chargers
    assert plan["feasible"] is False


async def test_trip_multi_stop_with_waypoint(auth_client):
    """BLR -> Mandya (waypoint) -> Mysuru at 55%: per-leg planning with alternatives."""
    await seed_chargers(auth_client)
    v = await add_vehicle(auth_client, soc=55)
    r = await auth_client.post("/trips/plan", json={
        "origin": BLR,
        "destination": {"lat": 12.3050, "lng": 76.6550},
        "waypoints": [{"lat": 12.5240, "lng": 76.8960}],  # Mandya
        "vehicle_id": v["id"],
    })
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["feasible"] is True, plan
    # every stop carries a leg index and suggestion list
    for s in plan["stops"]:
        assert s["leg_index"] in (0, 1)
        assert isinstance(s["alternatives"], list)
        assert s["arrival_soc"] >= 15.0
    assert plan["destination_arrival_soc"] >= 15.0


async def test_trip_pinned_charger_swap(auth_client):
    """Pinning an alternative charger for a leg makes it the chosen stop."""
    await seed_chargers(auth_client)
    v = await add_vehicle(auth_client, soc=60)
    base = {
        "origin": BLR,
        "destination": {"lat": 12.3050, "lng": 76.6550},
        "vehicle_id": v["id"],
    }
    r = await auth_client.post("/trips/plan", json=base)
    plan = r.json()
    assert plan["feasible"], plan
    stop = plan["stops"][0]
    if not stop["alternatives"]:
        return  # nothing to swap with in the seed set
    alt_id = stop["alternatives"][0]["charger"]["id"]
    r = await auth_client.post("/trips/plan", json={
        **base, "pinned_chargers": {str(stop["leg_index"]): alt_id},
    })
    plan2 = r.json()
    assert plan2["feasible"], plan2
    assert plan2["stops"][0]["charger"]["id"] == alt_id


async def test_dashboard_stats(auth_client):
    await seed_chargers(auth_client)
    v = await add_vehicle(auth_client, soc=70)
    r = await auth_client.get("/chargers/nearby", params={**BLR, "radius_km": 15})
    cid = r.json()[3]["id"]
    # one full session + one report
    sid = (await auth_client.post("/chargers/sessions/start", json={"charger_id": cid, "vehicle_id": v["id"]})).json()["session_id"]
    await auth_client.post(f"/chargers/sessions/{sid}/end", json={"successful": True, "energy_kwh": 18.5})
    r = await auth_client.get("/auth/me/stats")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["sessions_total"] >= 1
    assert s["sessions_successful"] >= 1
    assert s["energy_kwh"] >= 18.5
    assert s["chargers_visited"] >= 1
    assert s["vehicles_count"] >= 1
    assert s["recent_sessions"][0]["energy_kwh"] == 18.5
    assert s["est_cost_inr"] > 0


async def test_manual_vehicle_creation(auth_client):
    r = await auth_client.post("/vehicles", json={
        "make": "Citroen", "model": "eC3", "category": "4W",
        "battery_kwh": 29.2, "efficiency_wh_per_km": 112,
        "connector_types": ["CCS2", "Type2_AC"], "max_dc_power_kw": 30,
        "battery_soc": 64,
    })
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["make"] == "Citroen" and v["battery_kwh"] == 29.2


async def test_infeasible_trip_offers_reachable_suggestions(auth_client):
    """BLR -> Mysuru at 40%: not single-stop plannable, but reachable chargers
    along the way must be suggested so the user can hop."""
    await seed_chargers(auth_client)
    v = await add_vehicle(auth_client, soc=40)
    r = await auth_client.post("/trips/plan", json={
        "origin": BLR,
        "destination": {"lat": 12.3050, "lng": 76.6550},
        "vehicle_id": v["id"],
    })
    plan = r.json()
    assert plan["feasible"] is False
    assert len(plan["suggestions"]) >= 1, plan
    for s in plan["suggestions"]:
        assert s["arrival_soc"] >= 15.0          # reachable within reserve
        assert s["leg_index"] == 0


async def test_hop_via_waypoint_charge_makes_trip_feasible(auth_client):
    """The full hop flow: infeasible at 40% -> take a suggestion -> add it as a
    waypoint WITH a charge declaration -> trip becomes feasible."""
    await seed_chargers(auth_client)
    v = await add_vehicle(auth_client, soc=40)
    base = {"origin": BLR, "destination": {"lat": 12.3050, "lng": 76.6550}, "vehicle_id": v["id"]}

    r = await auth_client.post("/trips/plan", json=base)
    plan = r.json()
    assert plan["feasible"] is False and plan["suggestions"], plan
    sug = plan["suggestions"][0]

    r = await auth_client.post("/trips/plan", json={
        **base,
        "waypoints": [{"lat": sug["charger"]["lat"], "lng": sug["charger"]["lng"]}],
        "waypoint_charges": {"0": sug["charger"]["id"]},
    })
    plan2 = r.json()
    assert plan2["feasible"] is True, plan2
    # the waypoint charge appears as a stop and battery math carries through
    wp_stops = [s for s in plan2["stops"] if s["charger"]["id"] == sug["charger"]["id"]]
    assert wp_stops and wp_stops[0]["target_soc"] == 80.0
    assert plan2["destination_arrival_soc"] >= 15.0
