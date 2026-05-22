"""
Social media monitor.
Tracks engagement trends, detects drops/spikes, monitors follower changes.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger("manager.social-monitor")


async def get_fb_page_metrics() -> dict:
    """Fetch current Facebook page metrics."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{settings.FB_GRAPH_URL}/{settings.FB_PAGE_ID}",
                params={
                    "fields": "name,fan_count,followers_count,talking_about_count",
                    "access_token": settings.FB_PAGE_TOKEN,
                },
            )
            data = resp.json()
            if "error" in data:
                return {"success": False, "error": data["error"].get("message", "Unknown")}

            return {
                "success": True,
                "followers": data.get("followers_count", data.get("fan_count", 0)),
                "likes": data.get("fan_count", 0),
                "talking_about": data.get("talking_about_count", 0),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
    except Exception as e:
        logger.error(f"FB metrics failed: {e}")
        return {"success": False, "error": str(e)}


async def get_fb_recent_post_engagement(limit: int = 5) -> dict:
    """Fetch engagement on recent Facebook posts to detect trends."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{settings.FB_GRAPH_URL}/{settings.FB_PAGE_ID}/feed",
                params={
                    "fields": "id,message,created_time,likes.summary(true),comments.summary(true),shares",
                    "limit": limit,
                    "access_token": settings.FB_PAGE_TOKEN,
                },
            )
            data = resp.json()
            if "error" in data:
                return {"success": False, "error": data["error"].get("message", "Unknown")}

            posts = []
            total_engagement = 0
            for post in data.get("data", []):
                likes = post.get("likes", {}).get("summary", {}).get("total_count", 0)
                comments = post.get("comments", {}).get("summary", {}).get("total_count", 0)
                shares = post.get("shares", {}).get("count", 0)
                eng = likes + comments + shares
                total_engagement += eng

                posts.append({
                    "post_id": post.get("id", ""),
                    "message": (post.get("message", ""))[:80],
                    "created_time": post.get("created_time", ""),
                    "likes": likes,
                    "comments": comments,
                    "shares": shares,
                    "engagement": eng,
                })

            avg_engagement = total_engagement / len(posts) if posts else 0

            return {
                "success": True,
                "posts": posts,
                "total_engagement": total_engagement,
                "avg_engagement": round(avg_engagement, 1),
                "post_count": len(posts),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
    except Exception as e:
        logger.error(f"FB recent engagement failed: {e}")
        return {"success": False, "error": str(e)}


def detect_engagement_trend(current_avg: float, previous_avg: float, threshold: float = 0.3) -> dict:
    """Compare current vs previous engagement averages to detect drops or spikes."""
    if previous_avg <= 0:
        return {"trend": "no_baseline", "change_pct": 0}

    change_pct = (current_avg - previous_avg) / previous_avg

    if change_pct <= -threshold:
        trend = "dropping"
    elif change_pct >= threshold:
        trend = "spiking"
    else:
        trend = "stable"

    return {
        "trend": trend,
        "change_pct": round(change_pct * 100, 1),
        "current_avg": current_avg,
        "previous_avg": previous_avg,
    }


def detect_viral_post(posts: list, multiplier: float = 3.0) -> Optional[dict]:
    """Detect if any post has engagement significantly above average."""
    if len(posts) < 2:
        return None

    engagements = [p["engagement"] for p in posts]
    avg = sum(engagements) / len(engagements) if engagements else 0

    if avg <= 0:
        return None

    for post in posts:
        if post["engagement"] > avg * multiplier:
            return {
                "post_id": post["post_id"],
                "message": post["message"],
                "engagement": post["engagement"],
                "avg_engagement": round(avg, 1),
                "multiplier": round(post["engagement"] / avg, 1),
            }

    return None
