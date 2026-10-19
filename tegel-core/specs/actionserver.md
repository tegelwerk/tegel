# Action server

The action server runs registered actions on request and serves what they produced. This document is the contract for the action catalog, the run lifecycle and its phases, transfer plans, the HTTP API, authentication, and how results are found again. The reference implementation is `tegel-actionserver`, a standalone server that needs no data server deployment.

Decisions this contract rests on:

- A Tegel deployment has three parts. The data server serves on-disk catalogues read-only. The dashboard (the orchestrator) searches data and results and commands actions. The action server executes those commands and keeps their results.
- An action is described by a card. The card is the only thing a client needs to call it: inputs, parameters as JSON Schema, outputs, and the scope a caller must hold.
- One contract covers two kinds of action. Compute actions write to the action server's own results catalog. Catalog actions, such as ingest, write a data server's catalog and run on that data server's deployment. They differ in scope and in what they write, not in how they are called.
- The interface is HTTP with bearer tokens, as for the data server. An adapter for another protocol, such as one for language-model tools, is a client of this interface and is not part of it.
- Results are served with the data server's read API. The dashboard reads data and results with one client.
- Inputs are URIs. The action server reads them with the caller's authority, not with broader credentials of its own.

## Roles

| Part | Does | Writes |
| --- | --- | --- |
| Data server ([dataserver.md](dataserver.md)) | organizes on-disk catalogues and serves them | nothing through its read API |
| Dashboard | searches data and results, assembles selections, commands actions | nothing directly |
| Action server (this document) | runs actions, stores their results, makes them discoverable | its own results catalog; catalog actions write their data server's catalog through that deployment's pipeline |

A deployment may run one action server for compute actions and expose its data server's catalog actions through a second action server on the data server's deployment. Both implement this document.

## Responsibilities

The action server:

- lists the actions it can run, as cards
- validates a run request against the card before anything is queued
- runs actions and reports each run's state
- reads inputs with the caller's authority
- stores every finished run, successful or failed, as a resource in its results catalog
- serves that catalog with the data server read API
- records who asked for each run

The action server does not:

- change a data server's catalog except through a catalog action that the data server's deployment runs
- store a caller's token, or write it to a run, a log, or a result
- accept an action whose implementation is not installed as an entry point
- embed modality vocabulary; actions and their result schema belong to an incarnation

## Actions

An action is identified by `id` and `version`. `id` is lowercase letters, digits, `.`, `_` and `-`, starting with a letter or digit, for example `xrd.match` or `catalog.ingest`. `version` is the action's own version. A change to what an action computes is a new version.

A card (`ActionCard`) has:

| Field | Meaning |
| --- | --- |
| `id`, `version` | identity; `ref` is `id@version` |
| `title`, `description` | for people and for generated client tools |
| `inputs` | ports: `name`, `kind`, `description`, `optional` |
| `outputs` | ports: `name`, `kind`, `description`, `unit` for quantities, `optional` |
| `parameters` | JSON Schema of the parameters object |
| `scope` | the scope a caller needs to run it; default `actions:run` |
| `resources` | named resources a run holds while it executes, for example `gpu` or `catalog-writer` |
| `validity` | the domain the action is valid in and its known failure modes |
| `software` | packages whose versions each run records |
| `license` | the licence of the implementation, and of model weights when they differ |

Port kinds are `structure`, `trajectory`, `table`, `array`, `quantity`, `json`, `file`, and `selection`. A `selection` input is a list of URIs taken as one input. Input names and parameter names do not overlap.

### Catalog actions

A catalog action writes a data server's catalog. It:

- requires the scope `catalog:write`
- holds the resource `catalog-writer`, so at most one catalog action runs at a time for that catalog; this keeps the single-writer rule of [actions.md](actions.md)
- runs on the data server's deployment and calls that deployment's pipeline; the reference implementation's `catalog.ingest` starts the `ingest-directories` flow

Its result is a run resource like any other. The resources it changed are named in the run record, not copied into the results catalog.

## Runs

A run is one execution of one action version on one set of inputs and parameters.

| State | Meaning |
| --- | --- |
| `queued` | accepted, not started |
| `running` | executing |
| `succeeded` | finished; outputs stored |
| `failed` | finished without the declared outputs; `error` says why; the run is still stored |
| `cancelled` | stopped before it started |

A run moves from `queued` to `running` or `cancelled`, and from `running` to `succeeded` or `failed`. No other transition exists. A server that cannot interrupt a running action refuses to cancel it.

A server may serve a request from an earlier result. Its request key is a hash of the action ref, the input URIs, and the validated parameters. The new run is then `succeeded` at once, with `cached` true and `result` naming the earlier run's resource. `force` in the request disables this.

A server that restarts marks every `queued` and `running` run as `failed` with an error saying so.

Reuse is per principal: a run is served from an earlier result only when the same principal submitted that earlier, uncached, succeeded run. The earlier run read its inputs with that caller's authority, which another caller may not hold. A failed run is never reused.

### Phases

A running run is in one of three phases, reported as `phase` on the `Run` (`null` unless `running`):

| Phase | Does |
| --- | --- |
| `stage` | reads every input into a value the action can use (see [Inputs](#inputs)), using the caller's token |
| `work` | calls the action's `run(context, inputs, parameters)`; the caller's token has been dropped |
| `destage` | writes the outputs and the run resource to the results catalog (see [Results](#results)) |

A failure in any phase fails the run, and the failed run is still stored. Destage is the action server's own step: it does not bind URIs and does not use tegel-register's `register()`. It uses tegel-register only to describe array and table files the way Tiled registers them.

### Plans

`POST /plans` takes a run request and returns a `Plan` without queuing anything. For each input URI it gives an `InputPlan`: the route staging will take, the source (an origin, `file`, `results`, or a URI scheme), `size_bytes` when known without reading the payload, whether staging moves bytes, and an estimated `seconds`. The estimate divides the size by the source's measured throughput, or by `TEGEL_DEFAULT_THROUGHPUT` until a transfer from that source has been measured (`throughput_measured`). Every staged transfer updates the measurement.

`Plan.transfer_first` is true when the inputs that move bytes need longer than `TEGEL_TRANSFER_WARN_SECONDS` in total. `POST /runs` with such a request is refused with `409`, the detail, and the plan in `plan`; nothing is queued. The caller stages the data first, or resubmits with `"allow_transfer": true`. A moving input of unknown size is a warning, not a refusal. When the request would be served from an earlier result, `cached` names that result and no inputs are planned.

## HTTP API

Routes are relative to the server's configured base URL. Every route asks the active auth provider to accept the `Authorization` header; a missing or rejected token is `401`. Errors use `{"detail": "<message>"}`.

| Route | Scope | Success |
| --- | --- | --- |
| `GET /capabilities` | any | `Capabilities`, with `services` including `actions` |
| `GET /actions` | `actions:read` | list of `ActionCard` the caller may see |
| `GET /actions/{id}` | `actions:read` | the current `ActionCard` for `id` |
| `POST /plans` | the card's `scope` | `Plan`; nothing is queued |
| `POST /runs` | the card's `scope` | `202` and a `Run` in state `queued` (or `succeeded` when cached); `409` with the plan when `transfer_first` and not `allow_transfer` |
| `POST /batches` | the card's `scope` | `202` and a `Batch`: one run per URI of the one input given as a list |
| `GET /runs` | `actions:read` | list of `Run`, newest first; filters `action`, `state`, `batch`, `limit` |
| `GET /runs/{run}` | `actions:read` | `Run` |
| `POST /runs/{run}/wait?timeout=` | `actions:read` | `Run`, once it is final or the timeout (at most 600 s) passes |
| `DELETE /runs/{run}` | the card's `scope` | `Run` in state `cancelled`; `409` when the run is not `queued` |

`GET /capabilities` needs any accepted token; it reports `services` `["read", "actions"]` and the results profile's schema. `GET /healthz` needs none. Every other route not in this table is the results read API, which needs `actions:read`. The data server's `POST /refresh-catalog` needs `actions:admin` on an action server.

A request that names an unknown action, misses a required input, names an unknown input, gives an input that is not a non-empty string (for a `selection` port, a non-empty list of them), or fails the parameter schema is `400`, and nothing is queued. A caller without the card's scope gets `403`. `GET /actions` omits actions whose scope the caller lacks. An unknown run is `404`.

A run is visible to the principal that submitted it and to callers holding `actions:admin`. A deployment may widen that to a project.

### Run request

```json
{"action": "xrd.match",
 "inputs": {"structure": "https://actions.example/resources/r3f2a/data/structure/content",
            "measured": "https://data.example/api/v1/table/full/cu_sample_7/pattern"},
 "parameters": {"tolerance_deg": 0.1},
 "force": false,
 "allow_transfer": false}
```

`action` may be `id` or `id@version`. Without a version, the server's current version (the highest version installed) runs and the run records which one. Parameters are validated against the card's schema after its top-level defaults are filled in; the run records the filled-in values.

### Run

`Run` fields: `id`, `action` (the ref that ran), `state`, `phase`, `inputs`, `parameters` (validated, defaults filled in), `principal` (the submitting subject), `batch`, `submitted`, `started`, `ended`, `cached`, `result` (the run resource's URI, once final), `error`.

## Inputs

An input is a URI. Staging reads it when the run starts, by one of four routes:

| Route | URIs | Read |
| --- | --- | --- |
| `local` | `file:` under the server's readable storage (`TEGEL_READABLE_STORAGE`); a Tiled route or a data part URL on this server | from disk, or from the results catalog |
| `tiled` | a Tiled route on another server: `.../api/v1/{metadata,array/full,array/block,table/full,table/partition,node/full}/<path>` | `GET metadata`, then the array as `application/octet-stream` or the table as an Arrow file, with the caller's token |
| `bytes` | any other http(s) URL, such as a data server's `/resources/{id}/data/{part}/content` | streamed to the run's scratch directory, with the caller's token |
| `bind` | any other scheme (`globus:`, `zenodo:`, a site's Tiled source) | bound by tegel-register ([register.md](register.md)) into the action server's staging catalog, then read from there |

What a port receives depends on its kind: `array` an n-dimensional array, `table` a data frame, `json` and `quantity` parsed JSON (a Tiled node's metadata for a `json` port), and `structure`, `trajectory` and `file` a local path. An array file must be `.npy`; a table file `.parquet` or `.csv`. A `selection` port receives a list.

A `file:` input outside readable storage, an unknown result, an HTTP error, and a node whose family does not fit the port fail the run. None of these is a `400`, because whether an input resolves depends on the caller's access when the run starts.

A bind uses the credentials the binding layer's site configuration names for that source, not the caller's token. A run with an input on the `bind` route therefore needs the scope `actions:bind`; without it `POST /runs` is `403` and the plan says why. A bound node is kept in the staging catalog under a key derived from the URI, so later runs reuse it. Binds never write the results catalog or a data server's catalog.

## Authentication and tokens

The action server loads one `tegel.auth` provider, as the data server does. The provider returns a `Principal` with `subject` and `scopes`.

| Scope | Allows |
| --- | --- |
| `actions:read` | list actions and the caller's runs; read the results catalog |
| `actions:run` | submit and cancel compute actions |
| `catalog:write` | submit and cancel catalog actions |
| `actions:bind` | submit runs whose inputs must be bound by tegel-register |
| `actions:admin` | see every run; holds every `actions:*` scope |

A card may name another scope. The deployment's identity provider decides who holds which scope.

When an input lives on another server, the action server reads it with a token for the caller. With `TEGEL_TOKEN_EXCHANGE=forward`, it sends the caller's own bearer token, and only to origins listed in `TEGEL_FORWARD_ORIGINS`; other origins are read without a token. With `exchange`, it asks `TEGEL_TOKEN_EXCHANGE_URL` for a token whose audience is that origin (RFC 8693), authenticating as `TEGEL_TOKEN_EXCHANGE_CLIENT_ID`. It never reads a caller's input with credentials broader than the caller's. An API key accepted by the `apikey` provider is a credential for this server only and is never sent anywhere.

A token is held only in memory, only for the run it belongs to, and only until the stage phase ends. It is never written to a run, a log, a crate, a result, or an error message. A deployment that lets runs queue longer than a token lives uses token exchange with refresh, or keeps queues short.

The reference server registers a second provider, `apikey`, for standalone use: `TEGEL_API_KEYS` names a YAML file of SHA-256 key digests with a subject and scopes each. The `keycloak` provider takes scopes from the token's space-separated `scope` claim.

## Results

Every finished run is a resource in the action server's results catalog. The action server is that catalog's only writer. It serves the catalog with the read API of [dataserver.md](dataserver.md): `GET /search`, `GET /resources`, `GET /resources/{id}`, `GET /resources/{id}/metadata`, `GET /resources/{id}/data` and the data content routes, at the same base URL as the routes above. A dashboard reads data and results with the same client.

The run resource:

- **id** is the run id.
- **record** follows the results profile, which names an external LinkML schema like any other profile. The reference profile is packaged with the server (schema `https://w3id.org/tegel/actionserver/run`, class `ActionRun`, resource type `tegel.run`). Its slots are `run_id`, `action`, `action_version`, `action_ref`, `state`, `principal`, `submitted`, `started`, `ended`, `request_key`, `error`, `inputs`, `parameters`, `quantities` and `outputs`. `quantities` maps each quantity output to `{numeric_value, unit, uncertainty?, method?, conditions?}`; a quantity that is not a finite number fails the run.
- **parts** are the run's outputs, one part per output port, named after the port. Files are written under the server's outputs directory and referenced in place. An `array` is a `.npy` file and a `table` a `.parquet` file, both registered as externally managed Tiled children of the run node and served by Tiled. A `quantity` and a `json` output are `.json` byte parts; a `structure` given as an object is its JSON (`as_dict()` when it has one); a `file`, `structure` or `trajectory` given as a path is copied and served as bytes with its guessed media type. An output the card does not declare, or a missing output that is not `optional`, fails the run.
- **payload** is the run's RO-Crate (`application/ld+json`). Its provenance is PROV-O: the run `prov:used` each input URI, `prov:hadPlan` the card, `prov:wasAssociatedWith` the action server, and `prov:actedOnBehalfOf` the principal. Each output `prov:wasGeneratedBy` the run.
- **relations** are `{"rel": "used", "target": <input URI>}` for each input, so lineage is read the same way as on a data server.
- **provenance** has `plugin_id` set to the action ref and `source_name` set to the run id.

A failed run has the same record, with `state` `failed`, the error, and no parts for the outputs it did not produce.

## Registration

An action is installed, not configured. The entry-point group `tegel.actions` loads one action per entry point, sorted by entry-point name. An action is an object with:

- a `card` attribute, an `ActionCard`
- a `run(context, inputs, parameters)` method that returns the outputs by port name

Two entry points with the same `id` and `version` stop startup. A plugin that fails to load stops startup. Which worker or process runs an action is the server's business and is not visible in this contract.

The reference server's `@action` decorator (`tegel.actionserver.action`) builds such an object from a function: positional arguments after the context are the input ports, keyword-only arguments are the parameters, and their annotations and defaults become the parameter schema. The context gives the run id, a scratch `work_dir` removed after the run, a logger, and `cancelled()`.

The reference server runs actions on a pool of `TEGEL_ACTION_WORKERS` threads. A run starts when a worker and every resource on its card are free; `TEGEL_ACTION_RESOURCES` sets how many runs may hold each resource at once (one when unlisted), so a run waiting for `gpu` does not hold a worker.

## Configuration

The reference action server reads the environment. It does not need a data server's variables.

| Variable | Meaning |
| --- | --- |
| `TEGEL_PUBLIC_ORIGIN` | required; the base URL clients use, and the prefix of every result URI |
| `TEGEL_RESULTS_DIR` | required; holds the results catalog, the run table, outputs, the landing area and scratch space |
| `TEGEL_RESULTS_CATALOG_URI` | catalog the results are written to; default `sqlite:///<results dir>/results.db` |
| `TEGEL_STAGING_CATALOG_URI` | catalog binds land in; default `sqlite:///<results dir>/staging.db` |
| `TEGEL_RUNS_DB` | the run table; default `<results dir>/runs.db` |
| `TEGEL_RESULTS_PROFILE`, `TEGEL_RESULTS_SCHEMA_ROOT` | the results profile and its schema root; default the packaged profile |
| `TEGEL_AUTH` | `tegel.auth` provider; default `apikey` (`TEGEL_API_KEYS`), or `keycloak` (`TEGEL_OIDC_ISSUER`, `TEGEL_OIDC_AUDIENCE`, `TEGEL_OIDC_CLIENT_ID`) |
| `TEGEL_READABLE_STORAGE` | directories `file:` inputs may come from |
| `TEGEL_ACTIONS` | optional glob of action ids to serve; default all installed |
| `TEGEL_ACTION_WORKERS` | worker threads; default 2 |
| `TEGEL_ACTION_RESOURCES` | slots per resource, `gpu=1,catalog-writer=1` |
| `TEGEL_TOKEN_EXCHANGE` | `forward` (default) or `exchange` |
| `TEGEL_FORWARD_ORIGINS` | comma-separated origins that receive the caller's token in `forward` mode |
| `TEGEL_TOKEN_EXCHANGE_URL`, `TEGEL_TOKEN_EXCHANGE_CLIENT_ID`, `TEGEL_TOKEN_EXCHANGE_CLIENT_SECRET` | the token endpoint for `exchange` mode |
| `TEGEL_TRANSFER_WARN_SECONDS` | transfer time above which a plan says `transfer_first`; default 30 |
| `TEGEL_DEFAULT_THROUGHPUT` | bytes per second assumed for a source not yet measured; default 2e7 |
| `TEGEL_REGISTER_SITE` | tegel-register's site file for binds ([register.md](register.md)) |
| `TEGEL_SESSION_SECRET`, `TEGEL_HOST`, `TEGEL_PORT` | as for the data server; the port defaults to 8010 |

## Known limitations

- The results catalog is readable by every principal holding `actions:read`; run records are filtered by principal, the read API is not.
- A running run cannot be cancelled through the API. `context.cancelled()` is set only when the server stops.
- Binds use the site's credentials for a source, which is why they need `actions:bind`.
- Actions run in threads of the server process. A misbehaving action can slow the server; process isolation is a later executor.

## Not in this document

- A language-model tool adapter. Generate it from `GET /actions`; it calls this API.
- Where workers run and how they are scheduled.
- Projecting relations into Tiled's entity/link graph. As for the data server, relations live in the stored row.
