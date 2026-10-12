# tegel-actions

Prefect flows for the catalog pipeline. The data server does not run them.

`python -m tegel.actions.ingest` serves the ingest deployment. `python -m tegel.actions.update` serves the catalog updater, one run at a time. After each merge, the ingest flow calls `POST /refresh-catalog` on the data server.
