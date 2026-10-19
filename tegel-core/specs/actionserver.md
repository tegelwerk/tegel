# Action server

The action server runs registered actions on request and serves what they produced. This document is the contract for the action catalog, the run lifecycle, the HTTP API, authentication, and how results are found again.

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

## HTTP API

Routes are relative to the server's configured base URL. Every route asks the active auth provider to accept the `Authorization` header; a missing or rejected token is `401`. Errors use `{"detail": "<message>"}`.

| Route | Scope | Success |
| --- | --- | --- |
| `GET /capabilities` | any | `Capabilities`, with `services` including `actions` |
| `GET /actions` | `actions:read` | list of `ActionCard` the caller may see |
| `GET /actions/{id}` | `actions:read` | the current `ActionCard` for `id` |
| `POST /runs` | the card's `scope` | `202` and a `Run` in state `queued` (or `succeeded` when cached) |
| `POST /batches` | the card's `scope` | `202` and a `Batch`: one run per URI of the one input given as a list |
| `GET /runs` | `actions:read` | list of `Run`, newest first; filters `action`, `state`, `batch`, `limit` |
| `GET /runs/{run}` | `actions:read` | `Run` |
| `POST /runs/{run}/wait?timeout=` | `actions:read` | `Run`, once it is final or the timeout (at most 600 s) passes |
| `DELETE /runs/{run}` | the card's `scope` | `Run` in state `cancelled`; `409` when the run is not `queued` |

A request that names an unknown action, misses a required input, names an unknown input, gives an input that is not a non-empty string (for a `selection` port, a non-empty list of them), or fails the parameter schema is `400`, and nothing is queued. A caller without the card's scope gets `403`. `GET /actions` omits actions whose scope the caller lacks. An unknown run is `404`.

A run is visible to the principal that submitted it and to callers holding `actions:admin`. A deployment may widen that to a project.

### Run request

```json
{"action": "xrd.match",
 "inputs": {"structure": "https://actions.example/resources/3f2a/data/structure",
            "measured": "https://data.example/api/v1/metadata/cu_sample_7/pattern"},
 "parameters": {"tolerance_deg": 0.1},
 "force": false}
```

`action` may be `id` or `id@version`. Without a version, the server's current version runs and the run records which one.

### Run

`Run` fields: `id`, `action` (the ref that ran), `state`, `inputs`, `parameters` (validated, defaults filled in), `principal` (the submitting subject), `batch`, `submitted`, `started`, `ended`, `cached`, `result` (the run resource's URI, once final), `error`.

## Inputs

An input is a URI. The server resolves it when the run starts. Accepted forms:

- a data part on a data server: the part's `content_url`, or the Tiled node URL of a Tiled-served part
- a result of an earlier run: the run resource's data part URI on this or another action server
- any other URI that the deployment's binding layer can turn into a Tiled node; that layer is outside this document

A URI that cannot be resolved fails the run. It is not a `400`, because whether it resolves depends on the caller's access when the run starts.

## Authentication and tokens

The action server loads one `tegel.auth` provider, as the data server does. The provider returns a `Principal` with `subject` and `scopes`.

| Scope | Allows |
| --- | --- |
| `actions:read` | list actions and the caller's runs; read the results catalog |
| `actions:run` | submit and cancel compute actions |
| `catalog:write` | submit and cancel catalog actions |
| `actions:admin` | see every run |

A card may name another scope. The deployment's identity provider decides who holds which scope.

When an input lives on another Tegel server, the action server reads it with a token for the caller: the caller's own token, when its audience includes that server, or one obtained for the caller by token exchange (RFC 8693). It never reads a caller's input with credentials broader than the caller's. A token is held only in memory, only for the run it belongs to, and never written to a run, a log, a crate, or a result. A deployment that lets runs queue longer than a token lives uses token exchange with refresh, or resolves inputs at submission.

## Results

Every finished run is a resource in the action server's results catalog. The action server is that catalog's only writer. It serves the catalog with the read API of [dataserver.md](dataserver.md): `GET /search`, `GET /resources`, `GET /resources/{id}`, `GET /resources/{id}/metadata`, `GET /resources/{id}/data` and the data content routes, at the same base URL as the routes above. A dashboard reads data and results with the same client.

The run resource:

- **id** is the run id.
- **record** follows the results profile, which names an external LinkML schema like any other profile. The class has at least the slots `run_id`, `action`, `action_version`, `state`, `principal`, `submitted`, `started`, `ended` and `request_key`, plus whatever the incarnation's actions report, such as quantities.
- **parts** are the run's outputs, one part per output port. Arrays and tables are served by Tiled; everything else is served as bytes.
- **payload** is the run's RO-Crate (`application/ld+json`). Its provenance is PROV-O: the run `prov:used` each input URI, `prov:hadPlan` the card, `prov:wasAssociatedWith` the action server, and `prov:actedOnBehalfOf` the principal. Each output `prov:wasGeneratedBy` the run.
- **relations** are `{"rel": "used", "target": <input URI>}` for each input, so lineage is read the same way as on a data server.
- **provenance** has `plugin_id` set to the action ref and `source_name` set to the run id.

A failed run has the same record, with `state` `failed`, the error, and no parts for the outputs it did not produce.

## Registration

An action is installed, not configured. The entry-point group `tegel.actions` loads one action per entry point, sorted by entry-point name. An action is an object with:

- a `card` attribute, an `ActionCard`
- a `run(context, inputs, parameters)` method that returns the outputs by port name

Two entry points with the same `id` and `version` stop startup. A plugin that fails to load stops startup. Which worker or process runs an action is the server's business and is not visible in this contract.

## Configuration

The reference action server reads the environment, as the data server does. These are added to the data server's variables:

| Variable | Meaning |
| --- | --- |
| `TEGEL_RESULTS_CATALOG_URI` | catalog the results are written to |
| `TEGEL_RESULTS_PROFILE` | the results profile |
| `TEGEL_ACTIONS` | optional glob of action ids to serve; default all installed |
| `TEGEL_TOKEN_EXCHANGE` | `forward` (default) or `exchange` |

## Not in this document

- A language-model tool adapter. Generate it from `GET /actions`; it calls this API.
- Where workers run and how they are scheduled.
- Projecting relations into Tiled's entity/link graph. As for the data server, relations live in the stored row.
