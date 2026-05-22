from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    AGENT_NAME: str = "manager"
    API_PORT: int = 8100
    WP_URL: str = "https://pethubonline.com"
    WP_USER: str = "jasonsarah2026"
    WP_APP_PASSWORD: str = "EIul 3KqI 3fY7 yLbk Ltva aPnj"

    # Facebook
    FB_PAGE_ID: str = "1116722411522462"
    FB_PAGE_TOKEN: str = "EAANnapiZBRwUBRnGvBW38grDQwLOf95fc6mEWdI99ZCIUXNEMH9hdnNd592UyO1wDtK0MrhXDwo14q8bewy3m7M6V0lnFc6EqXlpUEVvRcmDDM9lZCatQPeZBZAsxp1ZABg0noSyphUbBONAnOQPdZAU7PL8ZCenZCB0hvsa5lf5vmTZC1QQT9XvaeE9faZBRr9VGTCsyZB4dYkGZCXNinnzQtpIn21J8ZBwkDrH6uuqPjVovjVwwZD"
    FB_GRAPH_URL: str = "https://graph.facebook.com/v21.0"

    # Agent endpoints
    SEO_AGENT_URL: str = "http://127.0.0.1:8101"
    ANALYTICS_AGENT_URL: str = "http://127.0.0.1:8102"
    SOCIAL_AGENT_URL: str = "http://127.0.0.1:8103"
    MAINTENANCE_AGENT_URL: str = "http://127.0.0.1:8104"

    # Monitoring intervals (seconds)
    MONITOR_INTERVAL: int = 300       # 5 minutes - main monitoring loop
    WP_POLL_INTERVAL: int = 600       # 10 minutes - WordPress content changes
    SOCIAL_POLL_INTERVAL: int = 900   # 15 minutes - social engagement checks
    HEALTH_CHECK_INTERVAL: int = 120  # 2 minutes - agent health checks

    # Cooldowns (seconds) - prevent duplicate dispatches
    POST_PUBLISH_COOLDOWN: int = 300       # 5 min after new page -> social post
    ENGAGEMENT_DROP_COOLDOWN: int = 3600   # 1 hour between engagement-drop actions
    BROKEN_LINK_FIX_COOLDOWN: int = 1800   # 30 min between broken link fixes
    PERFORMANCE_SCAN_COOLDOWN: int = 3600  # 1 hour between perf scans
    SEO_AUDIT_COOLDOWN: int = 1800         # 30 min between SEO audits

    # Thresholds
    ENGAGEMENT_DROP_THRESHOLD: float = 0.3     # 30% drop triggers action
    PERFORMANCE_TTFB_THRESHOLD_MS: int = 2000  # 2s TTFB triggers scan
    STALE_CONTENT_DAYS: int = 30               # 30 days = stale

    # Data persistence
    DB_PATH: str = "/var/lib/freelancer/projects/40416335/manager-agent/data/manager_data.json"

    HEARTBEAT_INTERVAL: int = 120

    class Config:
        env_file = ".env"


settings = Settings()
