# testing-com-data

Public data layer for the [nam-homefield/testing-com](https://github.com/nam-homefield/testing-com) private workflow repo.

## What's here

CSV datasets that AirOps workflows fetch at runtime via `raw.githubusercontent.com`. The private workflow repo can't be the fetch source because GitHub's raw CDN requires auth tokens for private repos. This public sibling repo holds only data — no workflow code, prompts, or secrets.

| File | Source | Refresh cadence | Owner |
|---|---|---|---|
| `data/labcorp_psc_hours.csv` | Scraped JSON-LD from `locations.labcorp.com` | Monthly (1st @ 5am PT) | testing-com `15_labcorp_hours_scrape.py` cron |
| `data/fqhc_per_city_enriched.csv` | HRSA bulk CSV + HRSA findahealthcenter API + Google Places API enrichment | Quarterly (Jan/Apr/Jul/Oct 1st @ 6am PT) | testing-com `17/18/19_*.py` cron |
| `data/labcorp_psc_neighborhoods.csv` _(pending)_ | OSM Nominatim reverse-geocode | Quarterly | testing-com `19_psc_neighborhood_lookup.py` cron |

## Why public

All data here is aggregated from public + properly-licensed sources (HRSA's open dataset, Labcorp's public sitemap, OpenStreetMap, Google Places API results that allow caching per Google's TOS). Nothing here is proprietary or sensitive. Making the repo public sidesteps the GitHub raw CDN auth issue without requiring secrets management in AirOps.

## Updating

Commits happen automatically via launchd cron jobs on the data-pipeline maintainer's machine. Manual edits welcome via PR but the next cron run will overwrite — submit a PR against the upstream scrape script in the private repo instead.
