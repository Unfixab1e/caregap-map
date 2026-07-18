-- Register CareGap Map tables in Unity Catalog from the uploaded volume files.
-- Run on a SQL warehouse after uploading (see DEPLOYMENT.md):
--   raw CSVs          -> /Volumes/<catalog>/<schema>/caregap_data/raw/
--   processed parquet -> /Volumes/<catalog>/<schema>/caregap_data/processed/
--
-- Replace the catalog/schema/volume below or run with -v substitutions.

USE CATALOG main;
USE SCHEMA caregap;

-- --- Processed outputs (what the app reads) -------------------------------

CREATE OR REPLACE TABLE facilities_scored AS
SELECT * FROM read_files(
  '/Volumes/main/caregap/caregap_data/processed/facilities_scored.parquet',
  format => 'parquet'
);

CREATE OR REPLACE TABLE region_summary_state AS
SELECT * FROM read_files(
  '/Volumes/main/caregap/caregap_data/processed/region_summary_state.parquet',
  format => 'parquet'
);

CREATE OR REPLACE TABLE region_summary_district AS
SELECT * FROM read_files(
  '/Volumes/main/caregap/caregap_data/processed/region_summary_district.parquet',
  format => 'parquet'
);

-- --- Raw inputs (optional: lets the pipeline run on Databricks) -----------

CREATE OR REPLACE TABLE facilities_raw AS
SELECT * FROM read_files(
  '/Volumes/main/caregap/caregap_data/raw/facilities.csv',
  format => 'csv', header => true, multiLine => true, escape => '"',
  schemaEvolutionMode => 'none', inferSchema => false
);

CREATE OR REPLACE TABLE pin_directory_raw AS
SELECT * FROM read_files(
  '/Volumes/main/caregap/caregap_data/raw/india_post_pincode_directory.csv',
  format => 'csv', header => true, inferSchema => false
);

CREATE OR REPLACE TABLE nfhs_raw AS
SELECT * FROM read_files(
  '/Volumes/main/caregap/caregap_data/raw/nfhs_5_district_health_indicators.csv',
  format => 'csv', header => true, inferSchema => false
);

-- --- Grants for the app's service principal -------------------------------
-- After `databricks apps create`, look up the app service principal id and:
-- GRANT USE CATALOG ON CATALOG main TO `<app-service-principal>`;
-- GRANT USE SCHEMA, SELECT ON SCHEMA main.caregap TO `<app-service-principal>`;
-- GRANT READ VOLUME ON VOLUME main.caregap.caregap_data TO `<app-service-principal>`;
