# Clarity session patterns

Friction patterns pulled from Microsoft Clarity session recordings for testing.com by
reading each session's event timeline as text. No recordings are watched.

`index.html` is a hand-built static report, not auto-generated. Unlike `orders/`, there is
no data file and no cron; regenerate by re-running the Clarity pulls and rebuilding the page.

Source: `POST clarity.microsoft.com/mcp/recordings/sample`, project y0aebsjypd, Data Export
bearer token at `~/.config/clarity/testing-com.token` on the Mac mini.

Always filter `country: ["United States"]` on the way in. The filter is inclusion-only and
the response carries no per-session country, so offshore traffic cannot be removed after the
fact. An unfiltered pull produced three false double-submit findings.

Served with `noindex,nofollow`. Carries conversion rates and order slugs. Order slugs are not
sensitive: `/checkout/{slug}/` returns a generic shell with no per-order content. Contains no
patient information.
