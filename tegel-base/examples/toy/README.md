# tegel-toy

Reference incarnation for a synthetic NumPy `.npy` array with a JSON sidecar. It is not a scientific modality.

It registers `toy-record` on three entry-point groups, all in `tegel.toy.dataserver`:

- `tegel.resource_types` — `RECORD`, the type `toy.record` version `1`, schema `https://w3id.org/tegel/toy/record`
- `tegel.extractors` — `ToyExtractor` claims `.npy` files. It merges the sidecar `<name>.json`, then adds `plugin_id`, `source_name`, `payload_uri`, `resource_type`, `resource_type_version`, and either `key` (from `options["key"]`) or, when the sidecar has no `uuid`, `uuid` set to the file stem.
- `tegel.readers` — `ToyReader` describes a `.npy` payload as an `array` from its header, without reading the data, and any other payload as `bytes`.

`schema/profile.yaml` names the class `Record` in `toy/record.yaml` under `TEGEL_SCHEMA_ROOT`. That schema is `tegel-base/examples/schemas/toy/record.yaml`, outside this package. The profile searches on `channel`, copies `channel` into the RO-Crate, and maps `sample_name`, `uuid`, and `recorded` to the Dataset `name`, `identifier`, and `dateCreated`.
