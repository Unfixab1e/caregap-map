"""Shared SQL-warehouse connection for Databricks-backed adapters.

Supports two auth modes, in this order:

1. Personal access / OAuth user token via ``DATABRICKS_TOKEN`` (local dev).
2. OAuth M2M via ``DATABRICKS_CLIENT_ID`` / ``DATABRICKS_CLIENT_SECRET`` -
   these are injected automatically for a Databricks App's service
   principal, so no token ever needs to appear in app.yaml.

The HTTP path may be given directly (``DATABRICKS_HTTP_PATH``) or derived
from ``DATABRICKS_WAREHOUSE_ID`` (injected by an app's sql-warehouse
resource via ``valueFrom``).
"""

from __future__ import annotations

import os


def resolve_http_path(http_path: str | None = None) -> str:
    """The warehouse HTTP path, from arg, env, or a warehouse-id resource."""
    path = http_path or os.environ.get("DATABRICKS_HTTP_PATH", "")
    if path:
        return path
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
    if warehouse_id:
        return f"/sql/1.0/warehouses/{warehouse_id}"
    return ""


def have_warehouse_credentials(host: str, token: str) -> bool:
    """True when either token auth or app service-principal OAuth can work."""
    if not host:
        return False
    if token:
        return True
    return bool(os.environ.get("DATABRICKS_CLIENT_ID") and os.environ.get("DATABRICKS_CLIENT_SECRET"))


def connect_warehouse(host: str, http_path: str, token: str):
    """Open a databricks-sql-connector connection using the available auth."""
    try:
        from databricks import sql as dbsql
    except ImportError as exc:
        raise ImportError(
            "The 'databricks-sql-connector' package is required. "
            'Install it with: pip install -e ".[databricks]"'
        ) from exc

    hostname = host.removeprefix("https://").rstrip("/")
    if token:
        return dbsql.connect(server_hostname=hostname, http_path=http_path, access_token=token)

    client_id = os.environ.get("DATABRICKS_CLIENT_ID", "")
    client_secret = os.environ.get("DATABRICKS_CLIENT_SECRET", "")
    if client_id and client_secret:
        try:
            from databricks.sdk.core import Config, oauth_service_principal
        except ImportError as exc:
            raise ImportError(
                "The 'databricks-sdk' package is required for service-principal "
                'OAuth. Install it with: pip install -e ".[databricks]"'
            ) from exc

        def _credentials_provider():
            return oauth_service_principal(
                Config(host=f"https://{hostname}", client_id=client_id, client_secret=client_secret)
            )

        return dbsql.connect(
            server_hostname=hostname,
            http_path=http_path,
            credentials_provider=_credentials_provider,
        )

    raise RuntimeError(
        "No Databricks credentials: set DATABRICKS_TOKEN (local) or run inside a "
        "Databricks App with an attached SQL-warehouse resource (service-principal "
        "OAuth via DATABRICKS_CLIENT_ID/SECRET)."
    )
