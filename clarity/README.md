# Clarity session patterns

Friction patterns pulled from Microsoft Clarity session recordings for testing.com by
reading each session's event timeline as text. No recordings are watched.

`index.html` is a hand-built static report, not auto-generated. Unlike `orders/`, there is
no data file and no cron; regenerate by re-running the Clarity pulls and rebuilding the page.

Source: `POST clarity.microsoft.com/mcp/recordings/sample`, project y0aebsjypd, Data Export
bearer token at `~/.config/clarity/testing-com.token` on the Mac mini.

The `count: 250` limit in the MCP server is client-side only (`z.number().lte(250)`).
The raw endpoint serves `count=10000` in about four seconds; 12000 returns HTTP 500. There
is no pagination, `offset` / `skip` / `page` are silently ignored, so use one large query.

Tracking on this project started mid-day 2026-08-18, so that is the earliest data. Playback
retention is 30 days, so the window grows on its own and reaches a rolling 30 days around
2026-09-17. Requesting an earlier start date returns nothing older; it is not a cap.

Always filter `country: ["United States"]` on the way in. The filter is inclusion-only and
the response carries no per-session country, so offshore traffic cannot be removed after the
fact. An unfiltered pull produced three false double-submit findings.

Served with `noindex,nofollow`. Carries conversion rates and order slugs. Order slugs are not
sensitive: `/checkout/{slug}/` returns a generic shell with no per-order content. Contains no
patient information.
