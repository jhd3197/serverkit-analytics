# Tests

These tests exercise the extension's backend (manifest shape, collector,
ingestion buffer, rollups, reports, log parsing, blueprint routes) but need
the **panel's Flask app and pytest fixtures** (`app`, `auth_headers`), so they
run from inside a ServerKit checkout rather than standalone:

```bash
# Symlink (or copy) this file into the panel's test suite:
ln -s "$(pwd)/test_analytics_extension.py" /path/to/ServerKit/backend/tests/

# Then run it from the panel backend:
cd /path/to/ServerKit/backend
pytest tests/test_analytics_extension.py
```

Via symlink the test resolves this repo's root with `os.path.realpath`; if you
copy the file instead, point it at the repo:

```bash
SERVERKIT_ANALYTICS_DIR=/path/to/serverkit-analytics pytest tests/test_analytics_extension.py
```
