# tegel-register

`tegel-register` is the package in [`tegel-register/`](../../tegel-register/README.md). Given a URI, it makes the data a node on a Tiled server that Tegel controls, so an action can compute on it. After that, every worker reads through a plain Tiled client. tegel-register is not on the read path, and it is not a destage step: an action's outputs are written by the action server, not bound from a URI.

## Where it sits

- **Action server, stage phase.** Before an action runs, each input that is not already a node on a Tiled server the action server can read is bound with `register()`. The plan comes first: `plan()` writes nothing, so a planner can call it for every input and report the cost before any bytes move.
- **Data server pipeline.** Staging (`tegel.actions.parts`) does not bind URIs; readers describe parts and staging registers them. It uses one helper from this package, `tegel.register.tiled._compat.generate_data_sources`, so that both use one call into Tiled's registration adapters (see [Tiled versions](#tiled-versions)).
- **Dashboard.** Not used. The dashboard asks the action server for a plan.

### Where a bind may write

A bind writes a node into the container passed as `into`. In a Tegel deployment, `into` is either a staging catalog or the action server's own catalog. It is never the data server's production catalog: that catalog is written only by the merge flow ([actions.md](actions.md)), and its rows carry the Tegel envelope, which a bind does not produce. The landing area is inside the action server's readable storage, not inside the data server's records.

### Cost before transfer

`Plan.size_bytes` is the number of bytes the bound node holds, when the binder can tell without moving them:

| Binder | Mode | Source of the size |
|---|---|---|
| file | REFERENCE, ACQUIRE | `stat` of the file, or the sum over a `?recursive` tree |
| tiled | EXISTING, PROXY, ACQUIRE | array shape × item size from the node's structure; `None` for tables and containers |
| globus | ACQUIRE | the `size` field of `operation_ls` for the path |
| zenodo | ACQUIRE | the record's file entry, which also gives the md5; `None` with a note when the record cannot be fetched |

`Plan.moves_bytes` is true when executing the plan copies data (ACQUIRE), and false for EXISTING, REFERENCE and PROXY. A planner multiplies `size_bytes` by the throughput it has measured for that source to estimate a transfer time; a plan that moves bytes and has no size is reported as unknown, never as free. tegel-register does not keep throughput figures.

### Tiled versions

tegel-register supports Tiled `>=0.2.18,<0.3`. Tiled 0.2.19 changed `generate_data_sources` from `(mimetype, dict_or_none, item, is_directory)` to `(mimetype, item, is_directory, size=None, parameters=None)`. `tegel.register.tiled._compat.generate_data_sources(adapter, mimetype, path, is_directory, dict_or_none, parameters=None)` reads the installed signature and calls the matching one. `parameters` (an HDF5 `dataset`) reaches only the newer signature. `_compat.py` is the only module that imports Tiled internals, and it refuses a Tiled outside the supported range on import.

## Modes

`mode="auto"` picks the first feasible mode in this order: EXISTING, REFERENCE, PROXY, ACQUIRE. A forced mode that is not feasible raises `ModeNotAvailableError` with `.feasible` and `.reasons`.

- REFERENCE: an external asset under readable storage.
- PROXY: a custom adapter forwards reads to an upstream Tiled server.
- ACQUIRE: land the bytes, then REFERENCE the copy.
- EXISTING: the data is already a node on the target server.

| Binder | EXISTING | REFERENCE | PROXY | ACQUIRE |
|---|---|---|---|---|
| file | no | path under a readable entry | no | landing configured |
| tiled | source base equals the target base | no | `proxy: true` and the family is array or table | landing configured; family is array or table |
| globus | no | no | no | `landing.globus` configured |
| zenodo | no | no | no | landing configured |

## Vocabulary

A bind turns a URI into a node on a Tiled server. A binder is a plugin for one protocol (`file`, `tiled`, `globus`, `zenodo`). A source is a site entry that binds a URI scheme to a protocol and its parameters. A site holds readable storage, path mapping, a landing area, and sources. A plan describes a bind, and `plan()` does not write. A binding is the result of executing a plan.

## Layout

`import tegel.register` does not import tiled, globus_sdk, numpy, pandas, or pyarrow. The core covers parse, redaction, errors, secrets, namespaces, site, context, the binder registry, and plan dataclasses.

`tegel.register.tiled` is client-side (`tiled.client`, `tiled.structures`) plus `tiled/_compat.py`. `_compat.py` imports Tiled internals. `tegel.register.tiled_server` is loaded by a Tiled server config. It imports `tiled.adapters.core`, `tiled.structures`, and redaction. It does not import binders, site, Globus, or Zenodo.

Binders use the core and `tegel.register.tiled`.

## Register

`plan()` describes a bind and does not write a node. `register()` executes that plan.

```python
plan(uri, into, *, key=None, mode="auto", metadata=None, context=None) -> Plan
register(uri, into, *, on_conflict="return", acquire_timeout=None, ...) -> Binding
```

1. Resolve namespace hops to `(resolved, trail)`.
2. Take the source from the site. The binder comes from `source.protocol`, or from the scheme when there is no source.
3. The binder plans the chosen mode.
4. `plan()` stops here, after attaching origin metadata.
5. EXISTING returns the node. It creates nothing and writes nothing.
6. ACQUIRE calls `binder.acquire`, then re-plans the landed path with the file binder in REFERENCE mode. Origin mode stays `acquire` and `acquired_from` is the resolved URI. A landed path that is not referenceable raises `SiteConfigError`.
7. `tegel.register.tiled.apply.create` writes the node.
8. Return the `Binding`.

`OnConflict.return` returns the existing node when the stored origin or resolved URI matches this registration, and raises `KeyConflictError` otherwise. It does not change the existing node's metadata. `error` always conflicts. `replace` deletes the node recursively with `external_only=True`, then creates it again.

Origin metadata is `metadata["tegel_register"]` plus spec `tegel-register-origin` version `1`. Caller metadata is merged first. A caller `tegel_register` key raises `ValueError`. Opaque namespace URIs are redacted in `origin` and `trail`. `resolved` is stored as given.

`BindContext` bootstraps the built-in binders when it uses the global registry. Client caches live on the context, keyed by `(protocol, base, secret_id)`, and are dropped on `close`.

## Site

Lookup order is an explicit path, then `TEGEL_REGISTER_SITE`, then `~/.config/tegel-register/site.yaml`. Entry-point sources merge under the file. An explicit path or env var is not merged with the user file.

Validation raises `SiteConfigError` naming the key. After load, readable paths are absolute and not nested. The landing directory is inside a readable entry. The layout contains `{name}` and `{sha12}` or `{sha64}`. An unknown protocol is an error at bind time, not at load time.

A site file may give `readable` local, `readable` server, and `landing.local` as paths relative to that file. Load joins them to the file's directory and does not follow symlinks. `from_mapping` requires those paths to already be absolute.

`to_server_path` resolves the file and each local root, picks the longest containing root, and joins onto the server root verbatim. Tiled compares readable-storage paths as text, so the server root is not resolved again.

The landing layout comes from the site file. There is no built-in layout string. An acquire writes a sibling `*.partial` file and renames it into place.

## Errors

The CLI exits 0 on success, 2 on usage, 3 on `BindError` or any other `URIResolverError`, 4 on `AccessError`, 5 on `SiteConfigError` or `PluginError`, and 1 on any other exception. `site check` fails when a readable local directory, a readable server directory, or the landing directory is missing.

Under `URIResolverError`: `InvalidURIError`, `UnknownSchemeError`, `SiteConfigError`, `PluginError` (`PluginConflictError`, `PluginVersionError`), `SecretLookupError`, namespace errors, access errors (`AuthenticationError`, `AuthorizationError`, `ResourceUnavailableError`), and `BindError` (`ModeNotAvailableError`, `NotReadableError`, `KeyConflictError`, `DescribeError`, `AcquireError`, `AcquireTimeoutError`).

Binder API version is 2. Any other `api_version` raises `PluginVersionError`. Two entry points for one protocol raise `PluginConflictError` the first time that protocol is used. Lazy proxies do not import their module at registration.

The CLI commands are `plan`, `register`, `site check`, `binders`, and `proxy credentials`. `proxy credentials` writes the mode-0600 upstream key file the server reads.

## Binders

File URIs follow RFC 8089. `?recursive` is the only allowed query. REFERENCE uses `describe_local`, which rewrites every asset `data_uri` onto the server path and raises `NotReadableError` if a rewritten URI leaves readable storage. A directory that is not one describable item needs `?recursive`. Undescribable files are skipped and recorded on `Plan.notes`.

Tiled URIs use a source with `protocol: tiled`. EXISTING applies when the normalized source base equals the target base. PROXY stores the upstream base as an external asset and the remote path as a parameter. The mimetypes are `application/x-tegel-register-tiled-proxy;structure=array` and `application/x-tegel-register-tiled-proxy;structure=table`. Upstream metadata is copied under `metadata["upstream"]`. ACQUIRE saves arrays with `numpy.save` of `node.read()` and tables with `to_parquet`, and notes that the array path reads the whole array. PROXY and ACQUIRE of containers are out of scope.

The proxy adapters resolve the remote node on each read, cache one client per upstream base, and raise `IncompatibleShapeError` when the shape, dtype, or columns differ. The server process, not the worker, holds `TEGEL_REGISTER_PROXY_CREDENTIALS`, a mode-0600 JSON file of upstream API keys. `tegel-register proxy credentials` writes that file. Registration still uses the site `secret_id`. A looser mode is refused. A missing entry uses an anonymous client. API keys never appear in exceptions. A client that reads the proxied node does not receive the upstream key.

`describe_local` follows Tiled's single-item registration (mimetype, `from_uris`, `generate_data_sources` or one asset) and rewrites paths. Site overrides live under `tiled.mimetypes_by_file_ext` and `tiled.adapters_by_mimetype`. On Tiled 0.2.18 a `.parquet` file is described as one external table asset, because Tiled's parquet adapter has no `from_uris` there. From 0.2.19 the adapter has one and `describe_local` uses it.

Zenodo is ACQUIRE only. URIs are `<scheme>:/records/<id>/<filename>`. `https://zenodo.org/records/...` is accepted only when a source sets `host: zenodo.org`. The `https` scheme is not claimed globally. A size or md5 mismatch raises `AcquireError` and leaves no file.

Globus is ACQUIRE only and requires `landing.globus`. A directory needs `?recursive`. The transfer goes to a temp sibling with checksum verification, then a local rename. `task_wait` timeouts and polling intervals are integers of at least 1. `timeout=None` repeats a 3600-second wait. A timeout cancels the task and raises `AcquireTimeoutError`. A missing local temp path after success is `SiteConfigError`. Every secret value named by the source is scrubbed from errors.

## Defaults

With `on_conflict="return"`, an existing key is returned when its stored origin or resolved URI matches this registration. A different origin raises `KeyConflictError`. The landing area is a directory inside readable storage, and registered assets use `Management.external` unless a data source names another management value. PROXY stores one API key per upstream base. Upstream structure drift fails the read.
