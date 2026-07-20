# serverkit-analytics

Privacy-first, self-hosted web analytics for [ServerKit](https://github.com/jhd3197/ServerKit)
— a lightweight (<4 KB) cookieless JavaScript tracker plus optional server-log
ingestion feeding a persistent time series stored on your own server. Nothing
leaves the box: visitor identity is a daily-rotating salted hash of IP+user-agent
(raw IPs are never stored), DoNotTrack is honored, and there are no cookies,
fingerprinting, or third-party calls.

Adds an **Analytics** page (`/analytics`) to the panel with visitors, pageviews,
top pages, referrers, devices, a live realtime counter, one-click tracker
injection into managed WordPress and nginx-proxied apps, scheduled rollups, and
configurable retention.

Installs from the ServerKit Marketplace (registry:
[serverkit-extensions](https://github.com/jhd3197/serverkit-extensions)).

## Repository layout

```
plugin.json               # extension manifest (routes, nav, jobs, SDK + panel gates)
backend/                  # Flask blueprint + collector/ingest/rollup/report services
  tracker/sk.js           #   readable tracker source (served fallback)
  tracker/sk.min.js       #   built artifact served at GET /api/v1/analytics/tracker.js
frontend/                 # runtime-ESM bundle source (vite lib build)
  runtime-entry.jsx       #   entry: injects inline CSS, exports AnalyticsPage
  components/             #   AnalyticsPage + tabs + local host-component stand-ins
  styles/analytics.css    #   page styles (host CSS custom properties)
scripts/build-tracker.mjs # tracker minifier (sk.js → sk.min.js; --check for drift)
scripts/build-zip.*       # release packaging (dist/serverkit-analytics-<version>.zip)
tests/                    # backend tests (run from a panel checkout — see tests/README.md)
dev/test-tracker.html     # manual tracker test page
```

## Development

The frontend is a **runtime-ESM extension**: it builds to a single
`frontend/dist/index.mjs` that the panel blob-imports at runtime — no panel
rebuild needed. React, react-router and `serverkit-sdk` are externalized and
resolved to the panel's own singletons via its import map; everything else
(lucide-react icons, recharts) is bundled.

```bash
cd frontend
npm install
npm run build        # writes frontend/dist/index.mjs
```

The tracker artifact is a checked-in build output. After editing
`backend/tracker/sk.js`:

```bash
node scripts/build-tracker.mjs          # rebuild backend/tracker/sk.min.js
node scripts/build-tracker.mjs --check  # CI-style drift check
```

Load it into a dev panel with **Marketplace → Plugins → Upload Zip**, or:

```bash
./scripts/build-zip.sh    # or scripts/build-zip.ps1 on Windows
# → dist/serverkit-analytics-<version>.zip
```

For API details and the contribution model see
[docs/EXTENSIONS.md](https://github.com/jhd3197/ServerKit/blob/main/docs/EXTENSIONS.md)
in the main repo; the feature-level write-up lives in
[docs/ANALYTICS.md](https://github.com/jhd3197/ServerKit/blob/main/docs/ANALYTICS.md).

## Release

Fully automated — no manual zips:

1. Bump `version` in `plugin.json` and push to `main` (or push a `vX.Y.Z` tag).
2. The **Create Release** workflow builds the bundle, zips it, creates the
   GitHub release with the zip attached, then downloads the published asset
   and upserts this extension's entry (version, URL, sha256) in
   [serverkit-extensions](https://github.com/jhd3197/serverkit-extensions).

One-time setup: add a `REGISTRY_TOKEN` secret (fine-grained PAT with
contents:write on `serverkit-extensions`) so the registry sync can push.
Without it the release still ships; only the registry update skips.

## License

MIT — see [LICENSE](LICENSE).
