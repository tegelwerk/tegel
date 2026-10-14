# tegel-toy

Reference incarnation for a synthetic record directory. It is not a scientific modality.

A record directory holds `record.json` and, optionally:

- `frames.zarr`, a Zarr v3 array;
- `stats.csv`, a CSV table with a header row;
- `notes.txt`, plain text.

It registers `toy-record` on three entry-point groups, all in `tegel.toy.dataserver`:

- `tegel.resource_types` — `RECORD`, the type `toy.record` version `1`, schema `https://w3id.org/tegel/toy/record`
- `tegel.extractors` — `ToyExtractor` claims files named `record.json`. It returns their fields as the `record`, with `uuid` set to the directory name when the file has none. The file's `relations` become the package's `relations`. It also returns `resource_type="toy.record"` and the file's `payload_uri`. Staging adds `plugin_id`, `source_name`, and the versions.
- `tegel.readers` — `ToyReader` lists the parts present next to `record.json`, without opening any data:
  - `frames`: `array`, `application/x-zarr`, designated, with its `shape` hint read from `zarr.json`;
  - `stats`: `table`, `text/csv;header=present`;
  - `notes`: `bytes`, `text/plain`.

  The payload is `record.json` itself, `application/json`.

Staging registers `frames` and `stats` with Tiled, so clients slice `frames` and select `stats` columns on Tiled's routes. Tegel serves `notes` and the payload as bytes, with HTTP Range.

`tegel.toy.dataserver.fixtures.write_record` writes a record directory for tests. Writing `frames` needs `numpy` and `zarr`, which are test dependencies only.

`schema/profile.yaml` names the class `Record` (schema version `"2"`) in `toy/record.yaml` under `TEGEL_SCHEMA_ROOT`. That schema is `tegel-base/examples/schemas/toy/record.yaml`, outside this package. It holds domain slots only: `sample_name`, `uuid`, `channel`, the boolean `calibrated`, and `recorded` from the mixin `Timestamped`. The profile searches on `channel` and `calibrated`, copies `channel`, `calibrated`, and `recorded` into the RO-Crate, and maps `sample_name`, `uuid`, and `recorded` to the Dataset `name`, `identifier`, and `dateCreated`. `uuid` is the catalog id.
