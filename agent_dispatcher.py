"""
Agent dispatcher - sends commands to the 4 child agents via their REST APIs.
Tracks dispatched tasks and respects cooldowns.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger("manager.dispatcher")

AGENT_URLS = {
    "seo": settings.SEO_AGENT_URL,
    "analytics": settings.ANALYTICS_AGENT_URL,
    "social": settings.SOCIAL_AGENT_URL,
    "maintenance": settings.MAINTENANCE_AGENT_URL,
}


async def check_agent_health(agent: str) -> dict:
    """Check if an agent is responding."""
    url = AGENT_URLS.get(agent)
    if not url:
        return {"agent": agent, "healthy": False, "error": "Unknown agent"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{url}/api/status")
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "agent": agent,
                    "healthy": True,
                    "status": data.get("status", "unknown"),
                    "data": data,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
            else:
                return {
                    "agent": agent,
                    "healthy": False,
                    "error": f"HTTP {resp.status_code}",
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
    except Exception as e:
        return {
            "agent": agent,
            "healthy": False,
            "error": str(e),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


async def check_all_agents() -> dict:
    """Health check all 4 agents."""
    results = {}
    for agent in AGENT_URLS:
        results[agent] = await check_agent_health(agent)
    return results


# ─── SEO Agent dispatches ─────────────────────────────────────────────────

async def dispatch_seo_audit() -> dict:
    """Trigger a full SEO audit."""
    return await _post("seo", "/api/audit/run", "SEO audit")


async def dispatch_seo_audit_page(page_id: int) -> dict:
    """Trigger SEO audit for a specific page."""
    return await _post("seo", f"/api/audit/page/{page_id}", f"SEO audit page {page_id}")


async def dispatch_seo_autofix() -> dict:
    """Trigger SEO auto-fix."""
    return await _post("seo", "/api/autofix/run", "SEO auto-fix")


async def dispatch_seo_schema_generation() -> dict:
    """Trigger schema generation."""
    return await _post("seo", "/api/schema/generate", "Schema generation")


async def dispatch_seo_link_analysis() -> dict:
    """Trigger internal link analysis."""
    return await _post("seo", "/api/links/analyze", "Internal link analysis")


async def dispatch_seo_freshness_refresh() -> dict:
    """Trigger content freshness refresh."""
    return await _post("seo", "/api/freshness/refresh", "Content freshness refresh")


# ─── Social Agent dispatches ──────────────────────────────────────────────

async def dispatch_social_post() -> dict:
    """Trigger an immediate social media post to both platforms."""
    return await _post("social", "/api/post/now", "Social post (both)")


async def dispatch_social_post_facebook() -> dict:
    """Trigger Facebook-only post."""
    return await _post("social", "/api/post/facebook", "Facebook post")


async def dispatch_social_reel() -> dict:
    """Trigger a Facebook Reel generation."""
    return await _post("social", "/api/reels/generate", "Reel generation")


async def dispatch_social_engagement_collection() -> dict:
    """Trigger engagement data collection."""
    return await _post("social", "/api/collect-engagement", "Engagement collection")


async def dispatch_social_ab_test() -> dict:
    """Start an A/B test."""
    return await _post("social", "/api/ab-test/start", "A/B test")


# ─── Maintenance Agent dispatches ─────────────────────────────────────────

async def dispatch_maintenance_link_scan() -> dict:
    """Trigger a broken link scan."""
    return await _post("maintenance", "/api/links/scan", "Link scan")


async def dispatch_maintenance_link_fix() -> dict:
    """Trigger broken link fix."""
    return await _post("maintenance", "/api/links/fix", "Link fix")


async def dispatch_maintenance_performance_scan() -> dict:
    """Trigger a performance scan."""
    return await _post("maintenance", "/api/performance/scan", "Performance scan")


async def dispatch_maintenance_security_scan() -> dict:
    """Trigger a security scan."""
    return await _post("maintenance", "/api/security/scan", "Security scan")


async def dispatch_maintenance_metadata_audit() -> dict:
    """Trigger metadata audit."""
    return await _post("maintenance", "/api/metadata/scan", "Metadata audit")


async def dispatch_maintenance_content_audit() -> dict:
    """Trigger content audit."""
    return await _post("maintenance", "/api/content/scan", "Content audit")


# ─── Analytics Agent dispatches ───────────────────────────────────────────

async def dispatch_analytics_collection() -> dict:
    """Trigger data collection."""
    return await _post("analytics", "/api/collect", "Analytics collection")


async def dispatch_analytics_scoring() -> dict:
    """Trigger agent scoring."""
    return await _post("analytics", "/api/scoring/run", "Agent scoring")


# ─── Helper ───────────────────────────────────────────────────────────────

async def _post(agent: str, path: str, label: str) -> dict:
    """Generic POST to an agent endpoint."""
    url = AGENT_URLS.get(agent)
    if not url:
        return {"success": False, "error": f"Unknown agent: {agent}"}

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{url}{path}")
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}

            success = resp.status_code in (200, 201, 202)
            if not success:
                logger.warning(f"Dispatch {label} to {agent} returned {resp.status_code}: {data}")
            else:
                logger.info(f"Dispatched {label} to {agent}: OK")

            return {
                "success": success,
                "agent": agent,
                "action": label,
                "status_code": resp.status_code,
                "response": data,
                "dispatched_at": datetime.now(timezone.utc).isoformat(),
            }
    except Exception as e:
        logger.error(f"Dispatch {label} to {agent} failed: {e}")
        return {
            "success": False,
            "agent": agent,
            "action": label,
            "error": str(e),
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
        }


async def _get(agent: str, path: str) -> Optional[dict]:
    """Generic GET from an agent endpoint."""
    url = AGENT_URLS.get(agent)
    if not url:
        return None

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{url}{path}")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.error(f"GET {agent}{path} failed: {e}")

    return None


async def get_seo_audit_results() -> Optional[dict]:
    """Fetch latest SEO audit results."""
    return await _get("seo", "/api/audit/results")


async def get_seo_issues() -> Optional[dict]:
    """Fetch all SEO issues."""
    return await _get("seo", "/api/audit/issues")


async def get_maintenance_link_report() -> Optional[dict]:
    """Fetch latest link scan report."""
    return await _get("maintenance", "/api/links/report")


async def get_maintenance_performance() -> Optional[dict]:
    """Fetch latest performance data."""
    return await _get("maintenance", "/api/performance")


async def get_social_status() -> Optional[dict]:
    """Fetch social agent status."""
    return await _get("social", "/api/status")


async def get_social_engagement() -> Optional[dict]:
    """Fetch social engagement summary."""
    return await _get("social", "/api/engagement")


async def get_analytics_status() -> Optional[dict]:
    """Fetch analytics agent status."""
    return await _get("analytics", "/api/status")
