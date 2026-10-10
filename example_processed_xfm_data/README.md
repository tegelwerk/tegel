# Processed measurement plugin

This is a plugin incarnation. Install it and the reference server loads it through entry points. It does not copy the Personalized Data Server stack, its secrets, or the measurement bytes.

The plugin claims a JSON record whose `cache` is `ProcessedCache`. `validate` returns false for anything else, so a later plugin can take that file. `process` copies attributes from `schema/measurement.yaml`. The reader returns shape and chunk shape from the designated store's `zarr.json` and does not open chunk files.

Search fields are `sample_code`, `campaign`, `cache`, `kind`, and `beam_energy_kev`.

```bash
uv sync
uv run pytest
uv run tegel-xfm
```

`tegel-xfm` starts the reference server on `http://127.0.0.1:8000` with this plugin loaded by entry point. It runs the extractor on `tests/fixtures/processed-leaf.json` and writes that one catalog record. A local OpenID issuer signs a bearer token for the process and stores it in `var/token`. The profile is `schema/profile.yaml`.

```bash
curl -H "Authorization: Bearer $(cat var/token)" \
  "http://127.0.0.1:8000/search?sample_code=AAPL-P1-leaf1"
```
