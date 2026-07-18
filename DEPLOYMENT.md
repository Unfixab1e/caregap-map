# Deploying CareGap Map to Databricks

Two supported paths. **Path A (volume-mounted Parquet)** is the fastest and needs zero
code changes; **Path B (Unity Catalog tables)** uses the `DatabricksDataSource` adapter
through a SQL warehouse.

> Status: these steps are written and reviewed but have **not been executed against a
> live workspace from this repo** (no workspace credentials on the dev machine). The
> adapter itself is unit-tested with an injected connection.

## Prerequisites

- Databricks workspace with Unity Catalog, **Apps** enabled, and (for Path B) a SQL
  warehouse.
- [Databricks CLI](https://docs.databricks.com/dev-tools/cli/) v0.220+ authenticated:
  `databricks auth login --host https://<workspace-host>`.
- Locally built processed data: `python scripts/build_processed_data.py`.

## 1. Upload the data to a Unity Catalog volume

```bash
databricks schemas create caregap main
databricks volumes create main caregap caregap_data MANAGED

# raw (only needed if the pipeline should run on Databricks) + processed
databricks fs cp -r data/raw    dbfs:/Volumes/main/caregap/caregap_data/raw
databricks fs cp -r data/processed dbfs:/Volumes/main/caregap/caregap_data/processed
```

The raw challenge CSVs stay out of Git either way; the volume is their home in the
workspace.

## 2. Create and deploy the app

```bash
databricks apps create caregap-map

# upload the source (excluding data/) to your workspace files
databricks sync . /Workspace/Users/<you>/caregap-map --exclude data --exclude reports

databricks apps deploy caregap-map \
  --source-code-path /Workspace/Users/<you>/caregap-map
```

`app.yaml` drives the launch command and environment.

## 3a. Path A — app reads Parquet from the volume (recommended first)

UC volumes are FUSE-mounted for Apps, so the existing `LocalDataSource` works unchanged.
In `app.yaml` set:

```yaml
env:
  - name: CAREGAP_DATA_SOURCE
    value: "local"
  - name: CAREGAP_DATA_DIR
    value: "/Volumes/main/caregap/caregap_data"
```

Grant the app's service principal `READ VOLUME` on `main.caregap.caregap_data`
(see the grants block in [scripts/register_tables.sql](scripts/register_tables.sql)),
then redeploy.

## 3b. Path B — app reads Unity Catalog tables via a SQL warehouse

1. Run [scripts/register_tables.sql](scripts/register_tables.sql) on your SQL warehouse
   (adjust catalog/schema/volume names).
2. In `app.yaml` set:

```yaml
env:
  - name: CAREGAP_DATA_SOURCE
    value: "databricks"
  - name: CAREGAP_DATABRICKS_CATALOG
    value: "main"
  - name: CAREGAP_DATABRICKS_SCHEMA
    value: "caregap"
  - name: DATABRICKS_HTTP_PATH
    value: "/sql/1.0/warehouses/<warehouse-id>"
```

   In a Databricks App, prefer a **SQL warehouse app resource** so `DATABRICKS_HOST`
   and credentials are injected for the app's service principal; otherwise provide
   `DATABRICKS_HOST`/`DATABRICKS_TOKEN` as app secrets — never commit them.
3. Grant the app's service principal `USE CATALOG` / `USE SCHEMA` / `SELECT` on
   `main.caregap` (grants block in the SQL file), then redeploy.

## 4. Re-running the pipeline on Databricks (optional)

The pipeline is plain pandas — it runs anywhere Python runs:

```bash
databricks jobs submit --json '{
  "run_name": "caregap-build",
  "tasks": [{
    "task_key": "build",
    "spark_python_task": {
      "python_file": "/Workspace/Users/<you>/caregap-map/scripts/build_processed_data.py",
      "parameters": ["--data-dir", "/Volumes/main/caregap/caregap_data"]
    },
    "environment_key": "default"
  }],
  "environments": [{
    "environment_key": "default",
    "spec": {"client": "1", "dependencies": ["pandas>=2.1", "pyarrow>=14", "pydantic>=2.6"]}
  }]
}'
```

Afterwards re-run the `facilities_scored` / `region_summary_*` statements from
`register_tables.sql` if you use Path B (Path A picks the new Parquet up on app restart).

## Local smoke test of the Databricks adapter

```python
from caregap_map.data_access import DatabricksDataSource
src = DatabricksDataSource()          # needs DATABRICKS_HOST/HTTP_PATH/TOKEN
src.load_region_summary("state")      # should return the 35-state summary
```
