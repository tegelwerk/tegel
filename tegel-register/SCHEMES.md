# Schemes

A source in the site file binds a URI scheme to a protocol. With no source, the scheme name is the protocol. Built-in protocols are `file`, `tiled`, `globus`, and `zenodo`.

## file

`file:///absolute/path` and `file://localhost/absolute/path` are local files (RFC 8089). Any other host is rejected. A percent-encoded slash is rejected. The only query is `?recursive`.

REFERENCE applies when the path sits under a readable entry. The asset URI is rewritten onto that entry's server path. ACQUIRE is feasible whenever landing is configured. `mode="auto"` selects REFERENCE when the path is under a readable entry, and selects ACQUIRE when the path is outside readable storage and landing is configured. A forced acquire copies the file into the landing area and then references the copy. Origin mode stays `acquire`.

A directory that is not one describable item needs `?recursive`. Hidden names are skipped. Files Tiled cannot describe are skipped and listed on `Plan.notes`.

## tiled

A source supplies `base_uri` and, when the server requires a key, `secret_id`. The URI path is the node path: `lab.tiled://images/scan`.

EXISTING applies when that base is the target server. PROXY applies when the source sets `proxy: true` and the node is an array or a table. The registered node stores the upstream base as its asset and the remote path as a parameter. ACQUIRE writes an array to `.npy` or a table to `.parquet` in the landing area, then references that file. The plan notes that an array acquire reads the whole array.

The target server must register the proxy adapters:

```yaml
application/x-tegel-register-tiled-proxy;structure=array: tegel.register.tiled_server.proxy:RemoteArrayAdapter
application/x-tegel-register-tiled-proxy;structure=table: tegel.register.tiled_server.proxy:RemoteTableAdapter
```

The server process holds upstream keys in the mode-0600 file named by `TEGEL_REGISTER_PROXY_CREDENTIALS`. `tegel-register proxy credentials` writes that file. Workers use the site `secret_id` only while registering and do not receive the key on read. A missing entry is anonymous. A shape, dtype, or column change fails the read.

## globus

A source supplies `collection` and `secret_id`. The URI path is absolute on that collection: `lab.globus:/data/sample.csv`. The only query is `?recursive`. A directory without it is rejected.

ACQUIRE transfers onto `landing.globus`, using a temp sibling and checksum verification, then renames the local file into the landing layout. `landing.local` and `landing.globus` must be the same storage. A successful task that does not appear locally raises `SiteConfigError`. A timeout cancels the task. Secret values named by the source are removed from errors.

## zenodo

`zenodo:/records/<id>/<filename>` downloads that file from the record. `https://zenodo.org/records/<id>/<filename>` does the same only when a source sets `protocol: zenodo` and `host: zenodo.org`. No other `https` host is accepted, and the `https` scheme is not a built-in protocol.

ACQUIRE checks the record's size and md5. A mismatch raises `AcquireError` and leaves no file. The download refuses a redirect onto another host.
