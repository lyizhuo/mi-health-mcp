"""MCP server exposing the shared query layer over Streamable HTTP."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mi_health.api.auth import require_current_scope
from mi_health.services import QueryService


def create_mcp(service: QueryService, allowed_hosts: list[str] | None = None) -> FastMCP:
    mcp = FastMCP(
        "mi-health",
        instructions=(
            "Read-only Xiaomi health data (steps, heart rate, sleep/HRV, workouts, body, "
            "SpO2, stress). Every response includes data_as_of, last_synced_at, and stale. "
            "Use sync_data only when you need a forced refresh."
        ),
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            allowed_hosts=allowed_hosts or ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"]
        ),
    )

    @mcp.tool()
    def get_connection_status() -> dict[str, Any]:
        """Return configuration and per-type sync/freshness status."""
        return service.status()

    @mcp.tool()
    def get_profile() -> dict[str, Any]:
        """Return the configured Xiaomi account identity (region/user id)."""
        creds = service.credentials()
        return {
            "status": "ok",
            "data": {"user_id": creds.user_id, "region": creds.region},
            "meta": {"source": "local_config"},
        }

    @mcp.tool()
    def get_daily_summary(start_date: str, end_date: str) -> dict[str, Any]:
        """Daily steps/distance/calories between start_date and end_date (YYYY-MM-DD)."""
        return service.summary(start_date, end_date)

    @mcp.tool()
    def query_metric_series(
        metric: str, start_date: str, end_date: str, granularity: str = "day"
    ) -> dict[str, Any]:
        """Metric series for steps, distance_m, or active_kcal; granularity day/week/month."""
        return service.metric_series(metric, start_date, end_date, granularity)

    @mcp.tool()
    def query_heart_rate(
        start_date: str,
        end_date: str,
        sample_type: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Heart-rate samples; sample_type can be resting, active, passive, or workout."""
        return service.heart_rate(start_date, end_date, sample_type, limit)

    @mcp.tool()
    def query_sleep(start_date: str, end_date: str, include_naps: bool = True) -> dict[str, Any]:
        """Sleep sessions with stages and overnight HRV aggregate fields."""
        return service.sleep(start_date, end_date, include_naps)

    @mcp.tool()
    def query_workouts(start_date: str, end_date: str) -> dict[str, Any]:
        """Workout summaries between start_date and end_date."""
        return service.workouts(start_date, end_date)

    @mcp.tool()
    def query_body_measurements(start_date: str, end_date: str) -> dict[str, Any]:
        """Body composition measurements between start_date and end_date."""
        return service.body_measurements(start_date, end_date)

    @mcp.tool()
    def query_spo2(start_date: str, end_date: str, limit: int | None = None) -> dict[str, Any]:
        """SpO2 samples between start_date and end_date."""
        return service.spo2(start_date, end_date, limit)

    @mcp.tool()
    def query_stress(start_date: str, end_date: str, limit: int | None = None) -> dict[str, Any]:
        """Stress samples between start_date and end_date."""
        return service.stress(start_date, end_date, limit)

    @mcp.tool()
    def query_abnormal_heart_beat(start_date: str, end_date: str) -> dict[str, Any]:
        """Abnormal heart-beat events between start_date and end_date."""
        return service.abnormal_heart_beat(start_date, end_date)

    @mcp.tool()
    def get_data_coverage() -> dict[str, Any]:
        """Local data coverage per data type (first/last timestamp, row count)."""
        return service.coverage()

    @mcp.tool()
    def sync_data(
        data_types: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Enqueue a sync job. Requires an API key with the `sync` scope."""
        require_current_scope("sync")
        return service.enqueue_sync(data_types, start_date, end_date, force)

    @mcp.tool()
    def get_sync_status(job_id: str) -> dict[str, Any]:
        """Return the status of a sync job created by sync_data."""
        return service.job(job_id)

    return mcp
