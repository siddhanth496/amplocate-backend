# Statiq importer

Pulls Statiq's public station data into the `chargers` table, normalised to the
same shape as the OCM / OSM / Google importers (`external_id = statiq-<id>`,
`operator = "Statiq"`, 75 m cross-source dedupe).

## Why this approach

Statiq does **not** publish a third-party / OCPI API — that needs a partnership.
Their per-station pages are server-rendered though, and their site explicitly
allows crawling:

```
# https://www.statiq.in/robots.txt
User-agent: *
Allow: /
Content-Signal: search=yes, ai-input=yes, ai-train=yes
Sitemap: https://www.statiq.in/sitemap.xml
```

So the importer discovers station URLs from the sitemap (or a city page), fetches
each page, and extracts the embedded `__NEXT_DATA__` JSON (with JSON-LD and a
Google-maps-link coordinate fallback).

## Legal / compliance — read before running at scale

- **robots.txt permits it**, but robots is not a licence. Review Statiq's
  [Terms of Service](https://www.statiq.in/termsandconditions-page) before
  **commercial** reuse of their data; if in doubt, email `support@statiq.in`
  — a data-sharing arrangement gives you cleaner data (live status, pricing)
  than crawling anyway.
- **Attribute the source.** Show "Data via Statiq" on Statiq-sourced chargers.
- **Stay polite.** The importer rate-limits itself (0.4 s/page, concurrency 4).
  Don't crank concurrency up; a full-network crawl is thousands of pages — run it
  off-peak and cache, don't re-crawl on every request.
- **It's live-ish, not live.** Connector status is a snapshot from page-load
  time. Re-run periodically (a nightly job) rather than treating it as realtime.

## Usage

First, **confirm the field mapping** on one station. Statiq's exact JSON keys are
read defensively; probe mode dumps what the parser extracted so you can verify
(and, if a key differs, adjust `normalize_station()` — it's the one
site-shape-specific function):

```bash
python -m app.seed.statiq_import --probe \
  https://www.statiq.in/monk-mansion-charging-station-ev-charging-station-id-4741
```

Then import:

```bash
# one city (fast, recommended to start)
python -m app.seed.statiq_import --city bengaluru-ev-charging-station

# the whole network via sitemap (thousands of pages — off-peak, optionally capped)
python -m app.seed.statiq_import --sitemap --max 500
```

Or via the admin API (dev-mode only, same auth as the other importers):

```bash
curl -X POST /admin/import/statiq -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' -d '{"city": "bengaluru-ev-charging-station"}'

curl -X POST /admin/import/statiq -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' -d '{"sitemap": true, "max": 500}'
```

`GET /admin/import/status` now reports `statiq_chargers` alongside the totals.

## What gets stored

| Field | Source |
|---|---|
| `external_id` | `statiq-<id>` (station id from the page/URL) |
| `operator` | `Statiq` |
| `connectors` | per-connector type + power, aggregated into `{type, power_kw, count}` |
| `price_per_kwh` | minimum ₹/kWh across the station's chargers |
| `status` | `online` if any connector is available/charging, `offline` if the station is unavailable, else `unknown` |
| `amenities` | e.g. `restroom`, `cafe` (lower-cased) |
| reliability | seeded at `0.6` with a small positive baseline (first-party live data), then adjusted by user reports like any other charger |

Re-running **upserts** existing Statiq rows (refreshes status/price/connectors)
and skips anything within 75 m of a charger already imported from another source.

## Maintenance

- If a probe run shows blank `chargers` or `lat/lng`, Statiq changed their page
  JSON — update the candidate key lists in `normalize_station()` (and, if needed,
  `_find_station_node()`). Tests in `tests/test_importers.py` cover the mapping.
- Consider a nightly scheduled job (e.g. `--city` per active city) rather than a
  single giant `--sitemap` crawl.
