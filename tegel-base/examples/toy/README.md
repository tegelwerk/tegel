# tegel-toy

Reference incarnation for a synthetic NumPy `.npy` array with a JSON sidecar. It is not a scientific modality.

It registers `toy-record` on three entry-point groups, all in `tegel.toy.dataserver`:

- `tegel.resource_types` — `RECORD`, the type `toy.record` version `1`, schema `https://w3id.org/tegel/toy/record`
- `tegel.extractors` — `ToyExtractor` claims `.npy` files. It returns the sidecar `<name>.json` as the `record`, with `uuid` set to the file stem when the sidecar has none. The sidecar's `relations` become the package's `relations`. It also returns `resource_type="toy.record"` and the file's `payload_uri`. Staging adds `plugin_id`, `source_name`, and the versions.
- `tegel.readers` — `ToyReader` describes the payload at `resource.payload_uri`: a `.npy` file as an `array` from its header, without reading the data, and any other payload as `bytes`.

`schema/profile.yaml` names the class `Record` (schema version `"2"`) in `toy/record.yaml` under `TEGEL_SCHEMA_ROOT`. That schema is `tegel-base/examples/schemas/toy/record.yaml`, outside this package. It holds domain slots only: `sample_name`, `uuid`, `channel`, the boolean `calibrated`, and `recorded` from the mixin `Timestamped`. The profile searches on `channel` and `calibrated`, copies `channel`, `calibrated`, and `recorded` into the RO-Crate, and maps `sample_name`, `uuid`, and `recorded` to the Dataset `name`, `identifier`, and `dateCreated`. `uuid` is the catalog id.
