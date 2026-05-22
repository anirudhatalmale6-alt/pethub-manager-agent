"""
Event Engine - the brain of the Smart Manager Agent.
Monitors all sources, detects events, and dispatches work to child agents.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import settings
from wp_monitor import fetch_recent_content, check_site_health
from social_monitor import (
    get_fb_page_metrics,
    get_fb_recent_post_engagement,
    detect_engagement_trend,
    detect_viral_post,
)
from agent_dispatcher import (
    check_all_agents,
    dispatch_seo_audit,
    dispatch_seo_audit_page,
    dispatch_seo_autofix,
    dispatch_social_post,
    dispatch_social_reel,
    dispatch_social_engagement_collection,
    dispatch_maintenance_link_scan,
    dispatch_maintenance_link_fix,
    dispatch_maintenance_performance_scan,
    dispatch_maintenance_security_scan,
    dispatch_maintenance_metadata_audit,
    dispatch_analytics_collection,
    get_seo_issues,
    get_maintenance_link_report,
    get_maintenance_performance,
    get_social_engagement,
)

logger = logging.getLogger("manager.event-engine")


class EventEngine:
    def __init__(self, state: dict):
        self.state = state
        self.events = state.get("events", [])
        self.dispatches = state.get("dispatches", [])
        self.cooldowns = state.get("cooldowns", {})
        self.wp_snapshot = state.get("wp_snapshot", {})
        self.social_snapshot = state.get("social_snapshot", {})

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _log_event(self, event_type: str, source: str, detail: str, severity: str = "info", data: Optional[dict] = None):
        event = {
            "type": event_type,
            "source": source,
            "detail": detail,
            "severity": severity,
            "data": data or {},
            "time": self._now(),
        }
        self.events.append(event)
        self.events = self.events[-500:]
        logger.info(f"EVENT [{severity}] {event_type}: {detail}")
        return event

    def _log_dispatch(self, action: str, agent: str, trigger: str, result: dict):
        dispatch = {
            "action": action,
            "agent": agent,
            "trigger": trigger,
            "success": result.get("success", False),
            "time": self._now(),
        }
        self.dispatches.append(dispatch)
        self.dispatches = self.dispatches[-500:]

    def _check_cooldown(self, key: str, cooldown_seconds: int) -> bool:
        """Returns True if the cooldown has expired (action is allowed)."""
        last = self.cooldowns.get(key)
        if not last:
            return True
        try:
            last_dt = datetime.fromisoformat(last)
            elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
            return elapsed >= cooldown_seconds
        except (ValueError, TypeError):
            return True

    def _set_cooldown(self, key: str):
        self.cooldowns[key] = self._now()

    def save_to_state(self):
        self.state["events"] = self.events
        self.state["dispatches"] = self.dispatches
        self.state["cooldowns"] = self.cooldowns
        self.state["wp_snapshot"] = self.wp_snapshot
        self.state["social_snapshot"] = self.social_snapshot

    # ─── WordPress monitoring ──────────────────────────────────────────

    async def monitor_wordpress(self):
        """Check for new/modified WordPress content."""
        wp_data = await fetch_recent_content(since_minutes=15)

        # New pages published
        for page in wp_data["new_pages"]:
            self._log_event(
                "new_page_published",
                "wordpress",
                f"New page: {page['title']} ({page['link']})",
                severity="info",
                data=page,
            )
            await self._handle_new_content(page)

        # New posts published
        for post in wp_data["new_posts"]:
            self._log_event(
                "new_post_published",
                "wordpress",
                f"New post: {post['title']} ({post['link']})",
                severity="info",
                data=post,
            )
            await self._handle_new_content(post)

        # Content modifications
        for page in wp_data["modified_pages"]:
            self._log_event(
                "content_modified",
                "wordpress",
                f"Page modified: {page['title']}",
                severity="info",
                data=page,
            )

        for post in wp_data["modified_posts"]:
            self._log_event(
                "content_modified",
                "wordpress",
                f"Post modified: {post['title']}",
                severity="info",
                data=post,
            )

        # Site health
        health = await check_site_health()
        if not health["reachable"]:
            self._log_event(
                "site_down",
                "wordpress",
                f"Site unreachable! Status: {health['status_code']}",
                severity="critical",
                data=health,
            )
        elif health["ttfb_ms"] > settings.PERFORMANCE_TTFB_THRESHOLD_MS:
            self._log_event(
                "slow_performance",
                "wordpress",
                f"High TTFB: {health['ttfb_ms']}ms (threshold: {settings.PERFORMANCE_TTFB_THRESHOLD_MS}ms)",
                severity="warning",
                data=health,
            )
            await self._handle_slow_performance(health)

        self.wp_snapshot = {
            "content_check": wp_data,
            "health": health,
            "checked_at": self._now(),
        }

    async def _handle_new_content(self, content: dict):
        """React to new content: trigger social post + SEO audit."""
        content_id = content.get("id", 0)

        # Trigger social post for new content
        if self._check_cooldown(f"social_new_{content_id}", settings.POST_PUBLISH_COOLDOWN):
            self._set_cooldown(f"social_new_{content_id}")
            result = await dispatch_social_post()
            self._log_dispatch("social_post_for_new_content", "social", f"new content: {content['title']}", result)

        # Trigger SEO audit for the new page
        if self._check_cooldown(f"seo_page_{content_id}", settings.SEO_AUDIT_COOLDOWN):
            self._set_cooldown(f"seo_page_{content_id}")
            result = await dispatch_seo_audit_page(content_id)
            self._log_dispatch("seo_audit_page", "seo", f"new content: {content['title']}", result)

    async def _handle_slow_performance(self, health: dict):
        """React to slow performance."""
        if self._check_cooldown("perf_scan", settings.PERFORMANCE_SCAN_COOLDOWN):
            self._set_cooldown("perf_scan")
            result = await dispatch_maintenance_performance_scan()
            self._log_dispatch("performance_scan", "maintenance", f"slow TTFB: {health['ttfb_ms']}ms", result)

    # ─── Social monitoring ─────────────────────────────────────────────

    async def monitor_social(self):
        """Check social engagement trends."""
        engagement = await get_fb_recent_post_engagement(limit=5)
        if not engagement.get("success"):
            return

        current_avg = engagement.get("avg_engagement", 0)
        prev_avg = self.social_snapshot.get("avg_engagement", 0)

        # Detect trend
        trend = detect_engagement_trend(current_avg, prev_avg, settings.ENGAGEMENT_DROP_THRESHOLD)

        if trend["trend"] == "dropping":
            self._log_event(
                "engagement_dropping",
                "social",
                f"Engagement dropped {abs(trend['change_pct'])}% (avg {current_avg} vs prev {prev_avg})",
                severity="warning",
                data=trend,
            )
            await self._handle_engagement_drop(trend)

        elif trend["trend"] == "spiking":
            self._log_event(
                "engagement_spiking",
                "social",
                f"Engagement spiking +{trend['change_pct']}% (avg {current_avg} vs prev {prev_avg})",
                severity="info",
                data=trend,
            )

        # Check for viral post
        viral = detect_viral_post(engagement.get("posts", []))
        if viral:
            self._log_event(
                "viral_post_detected",
                "social",
                f"Viral post detected: {viral['engagement']} eng ({viral['multiplier']}x avg) - {viral['message']}",
                severity="info",
                data=viral,
            )
            await self._handle_viral_post(viral)

        # Update page metrics
        page_metrics = await get_fb_page_metrics()
        prev_followers = self.social_snapshot.get("followers", 0)
        if page_metrics.get("success"):
            new_followers = page_metrics.get("followers", 0)
            if prev_followers > 0 and new_followers > prev_followers:
                gained = new_followers - prev_followers
                self._log_event(
                    "followers_gained",
                    "social",
                    f"Gained {gained} new followers (now {new_followers})",
                    severity="info",
                    data={"gained": gained, "total": new_followers},
                )

        self.social_snapshot = {
            "avg_engagement": current_avg,
            "followers": page_metrics.get("followers", prev_followers) if page_metrics.get("success") else prev_followers,
            "last_posts": engagement.get("posts", []),
            "trend": trend,
            "checked_at": self._now(),
        }

    async def _handle_engagement_drop(self, trend: dict):
        """React to engagement drop: schedule extra post or reel."""
        if self._check_cooldown("engagement_drop_action", settings.ENGAGEMENT_DROP_COOLDOWN):
            self._set_cooldown("engagement_drop_action")
            result = await dispatch_social_post()
            self._log_dispatch("extra_social_post", "social", f"engagement drop: {trend['change_pct']}%", result)

    async def _handle_viral_post(self, viral: dict):
        """React to viral post: boost similar content."""
        if self._check_cooldown("viral_boost", settings.ENGAGEMENT_DROP_COOLDOWN):
            self._set_cooldown("viral_boost")
            result = await dispatch_social_engagement_collection()
            self._log_dispatch("engagement_collection", "social", f"viral post: {viral['engagement']} engagement", result)

    # ─── Agent health monitoring ───────────────────────────────────────

    async def monitor_agent_health(self) -> dict:
        """Check all agent health statuses."""
        health = await check_all_agents()

        for agent, status in health.items():
            if not status.get("healthy"):
                self._log_event(
                    "agent_unhealthy",
                    f"agent:{agent}",
                    f"{agent} agent is DOWN: {status.get('error', 'unknown')}",
                    severity="critical",
                    data=status,
                )
            else:
                agent_data = status.get("data", {})
                errors = agent_data.get("recent_errors", 0)
                if errors > 5:
                    self._log_event(
                        "agent_high_errors",
                        f"agent:{agent}",
                        f"{agent} agent has {errors} recent errors",
                        severity="warning",
                        data=status,
                    )

        return health

    # ─── Cross-agent intelligence ──────────────────────────────────────

    async def cross_agent_analysis(self):
        """Analyze data across agents to detect compound issues."""
        # Check if SEO has unresolved broken links
        seo_issues = await get_seo_issues()
        if seo_issues:
            broken_link_issues = [i for i in seo_issues.get("issues", []) if "broken" in i.get("issue", "").lower()]
            if len(broken_link_issues) > 3:
                self._log_event(
                    "many_broken_links",
                    "cross-agent",
                    f"SEO audit found {len(broken_link_issues)} broken link issues",
                    severity="warning",
                    data={"count": len(broken_link_issues)},
                )
                if self._check_cooldown("broken_link_batch_fix", settings.BROKEN_LINK_FIX_COOLDOWN):
                    self._set_cooldown("broken_link_batch_fix")
                    result = await dispatch_seo_autofix()
                    self._log_dispatch("seo_autofix", "seo", f"{len(broken_link_issues)} broken links detected", result)

        # Check maintenance for broken links too
        link_report = await get_maintenance_link_report()
        if link_report and isinstance(link_report, dict):
            broken_count = link_report.get("broken_count", 0)
            if broken_count > 0:
                self._log_event(
                    "maintenance_broken_links",
                    "cross-agent",
                    f"Maintenance found {broken_count} broken links",
                    severity="warning",
                    data={"broken_count": broken_count},
                )
                if self._check_cooldown("maintenance_link_fix", settings.BROKEN_LINK_FIX_COOLDOWN):
                    self._set_cooldown("maintenance_link_fix")
                    result = await dispatch_maintenance_link_fix()
                    self._log_dispatch("maintenance_link_fix", "maintenance", f"{broken_count} broken links", result)

        # Check performance data
        perf = await get_maintenance_performance()
        if perf and isinstance(perf, dict):
            avg_ttfb = perf.get("avg_ttfb_ms", 0)
            if avg_ttfb > settings.PERFORMANCE_TTFB_THRESHOLD_MS:
                self._log_event(
                    "high_avg_ttfb",
                    "cross-agent",
                    f"Average TTFB is {avg_ttfb}ms (threshold: {settings.PERFORMANCE_TTFB_THRESHOLD_MS}ms)",
                    severity="warning",
                    data={"avg_ttfb_ms": avg_ttfb},
                )

    # ─── Full monitoring cycle ─────────────────────────────────────────

    async def run_full_cycle(self) -> dict:
        """Run a complete monitoring cycle across all sources."""
        cycle_start = self._now()
        logger.info("Starting full monitoring cycle...")

        results = {
            "wordpress": None,
            "social": None,
            "agent_health": None,
            "cross_agent": None,
        }

        try:
            await self.monitor_wordpress()
            results["wordpress"] = "ok"
        except Exception as e:
            logger.error(f"WordPress monitoring failed: {e}")
            results["wordpress"] = f"error: {e}"

        try:
            await self.monitor_social()
            results["social"] = "ok"
        except Exception as e:
            logger.error(f"Social monitoring failed: {e}")
            results["social"] = f"error: {e}"

        try:
            health = await self.monitor_agent_health()
            results["agent_health"] = {
                agent: status.get("healthy", False) for agent, status in health.items()
            }
        except Exception as e:
            logger.error(f"Agent health monitoring failed: {e}")
            results["agent_health"] = f"error: {e}"

        try:
            await self.cross_agent_analysis()
            results["cross_agent"] = "ok"
        except Exception as e:
            logger.error(f"Cross-agent analysis failed: {e}")
            results["cross_agent"] = f"error: {e}"

        self.save_to_state()

        cycle_end = self._now()
        logger.info(f"Monitoring cycle complete: {results}")

        return {
            "cycle_start": cycle_start,
            "cycle_end": cycle_end,
            "results": results,
            "events_this_cycle": len([
                e for e in self.events
                if e["time"] >= cycle_start
            ]),
            "dispatches_this_cycle": len([
                d for d in self.dispatches
                if d["time"] >= cycle_start
            ]),
        }
