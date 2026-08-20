# Testing.com orders report

Auto-published hourly by `orders-reporting/hourly_cron.sh` in the testing-com repo.

`index.html` is a stable shell whose bytes do not change between builds; every number
lives in `orders-data.json`, which the page fetches cache-busted on load. That split is
what lets the URL stay the same forever. Do not hand-edit either file, both are
overwritten hourly.

Served with `noindex,nofollow`. Carries revenue figures. Contains no patient information.
