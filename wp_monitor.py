"""
WordPress content monitor.
Detects new pages/posts, content changes, and plugin updates.
"""

import base64
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger("manager.wp-monitor")

WP_HEADERS = {
    "Authorization": "Basic "
    + base64.b64encode(
        f"{settings.WP_USER}:{settings.WP_APP_PASSWORD}".encode()
    ).decode(),
}


async def fetch_recent_content(since_minutes: int = 15) -> dict:
    """Fetch WordPress pages and posts modified in the last N minutes."""
    since = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    since_iso = since.isoformat()

    new_pages = []
    modified_pages = []
    new_posts = []
    modified_posts = []

    async with httpx.AsyncClient(timeout=20) as client:
        for content_type in ["pages", "posts"]:
            try:
                resp = await client.get(
                    f"{settings.WP_URL}/wp-json/wp/v2/{content_type}",
                    headers=WP_HEADERS,
                    params={
                        "per_page": 50,
                        "status": "publish",
                        "orderby": "modified",
                        "order": "desc",
                        "modified_after": since_iso,
                    },
                )
                if resp.status_code != 200:
                    continue

                items = resp.json()
                for item in items:
                    entry = {
                        "id": item["id"],
                        "title": item.get("title", {}).get("rendered", ""),
                        "slug": item.get("slug", ""),
                        "link": item.get("link", ""),
                        "modified": item.get("modified_gmt", ""),
                        "date": item.get("date_gmt", ""),
                        "type": "page" if content_type == "pages" else "post",
                    }

                    created = item.get("date_gmt", "")
                    modified = item.get("modified_gmt", "")

                    try:
                        created_dt = datetime.fromisoformat(created)
                        modified_dt = datetime.fromisoformat(modified)
                        if created_dt.tzinfo is None:
                            created_dt = created_dt.replace(tzinfo=timezone.utc)
                        if modified_dt.tzinfo is None:
                            modified_dt = modified_dt.replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        continue

                    is_new = (datetime.now(timezone.utc) - created_dt).total_seconds() < since_minutes * 60

                    if content_type == "pages":
                        if is_new:
                            new_pages.append(entry)
                        else:
                            modified_pages.append(entry)
                    else:
                        if is_new:
                            new_posts.append(entry)
                        else:
                            modified_posts.append(entry)

            except Exception as e:
                logger.error(f"Failed to fetch {content_type}: {e}")

    return {
        "new_pages": new_pages,
        "modified_pages": modified_pages,
        "new_posts": new_posts,
        "modified_posts": modified_posts,
        "total_new": len(new_pages) + len(new_posts),
        "total_modified": len(modified_pages) + len(modified_posts),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def get_all_published_count() -> dict:
    """Get total published pages and posts count."""
    counts = {"pages": 0, "posts": 0}

    async with httpx.AsyncClient(timeout=15) as client:
        for content_type in ["pages", "posts"]:
            try:
                resp = await client.head(
                    f"{settings.WP_URL}/wp-json/wp/v2/{content_type}",
                    headers=WP_HEADERS,
                    params={"status": "publish", "per_page": 1},
                )
                total = int(resp.headers.get("X-WP-Total", 0))
                counts[content_type] = total
            except Exception as e:
                logger.error(f"Failed to get {content_type} count: {e}")

    return counts


async def check_site_health() -> dict:
    """Quick health check on the WordPress site."""
    result = {
        "reachable": False,
        "status_code": 0,
        "ttfb_ms": 0,
        "api_reachable": False,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            import time
            start = time.monotonic()
            resp = await client.get(settings.WP_URL)
            elapsed = (time.monotonic() - start) * 1000

            result["reachable"] = resp.status_code == 200
            result["status_code"] = resp.status_code
            result["ttfb_ms"] = round(elapsed)
        except Exception as e:
            logger.error(f"Site health check failed: {e}")

        try:
            resp = await client.get(
                f"{settings.WP_URL}/wp-json/wp/v2/pages",
                headers=WP_HEADERS,
                params={"per_page": 1},
            )
            result["api_reachable"] = resp.status_code == 200
        except Exception:
            pass

    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    return result


async def check_sitemap() -> dict:
    """Check the sitemap for any issues."""
    result = {"url_count": 0, "errors": [], "checked_at": datetime.now(timezone.utc).isoformat()}

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(f"{settings.WP_URL}/sitemap.xml")
            if resp.status_code == 200:
                import re
                urls = re.findall(r"<loc>(.*?)</loc>", resp.text)
                result["url_count"] = len(urls)
            else:
                result["errors"].append(f"Sitemap returned {resp.status_code}")
        except Exception as e:
            result["errors"].append(f"Sitemap check failed: {e}")

    return result
