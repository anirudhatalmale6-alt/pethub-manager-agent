"""
PetHub Smart Manager Agent - Central Brain
Monitors the entire ecosystem 24/7 and dispatches work to child agents in real-time.
"""

import json
import os
import logging
import asyncio
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from config import settings
from event_engine import EventEngine
from agent_dispatcher import (
    check_all_agents,
    dispatch_seo_audit,
    dispatch_seo_autofix,
    dispatch_social_post,
    dispatch_social_reel,
    dispatch_maintenance_link_scan,
    dispatch_maintenance_performance_scan,
    dispatch_maintenance_security_scan,
    dispatch_analytics_collection,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("manager-agent")

scheduler = AsyncIOScheduler()
UK_TZ = pytz.timezone("Europe/London")

# ─── In-memory state (persisted to JSON) ────────────────────────────────────

state = {
    "events": [],
    "dispatches": [],
    "cooldowns": {},
    "wp_snapshot": {},
    "social_snapshot": {},
    "agent_health": {},
    "cycle_history": [],
    "started_at": None,
    "total_cycles": 0,
    "total_events": 0,
    "total_dispatches": 0,
    "errors": [],
}

engine: EventEngine = None


def load_state():
    os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
    if os.path.exists(settings.DB_PATH):
        try:
            with open(settings.DB_PATH, "r") as f:
                data = json.load(f)
                for key in state:
                    if key in data:
                        state[key] = data[key]
                state["events"] = state["events"][-500:]
                state["dispatches"] = state["dispatches"][-500:]
                state["cycle_history"] = state["cycle_history"][-200:]
                state["errors"] = state["errors"][-50:]
                logger.info(f"Loaded state: {state['total_cycles']} cycles, {state['total_events']} events")
        except Exception as e:
            logger.error(f"Failed to load state: {e}")


def save_state():
    os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
    try:
        with open(settings.DB_PATH, "w") as f:
            json.dump(
                {
                    "events": state["events"][-500:],
                    "dispatches": state["dispatches"][-500:],
                    "cooldowns": state["cooldowns"],
                    "wp_snapshot": state["wp_snapshot"],
                    "social_snapshot": state["social_snapshot"],
                    "agent_health": state["agent_health"],
                    "cycle_history": state["cycle_history"][-200:],
                    "started_at": state["started_at"],
                    "total_cycles": state["total_cycles"],
                    "total_events": state["total_events"],
                    "total_dispatches": state["total_dispatches"],
                    "errors": state["errors"][-50:],
                },
                f,
                default=str,
            )
    except Exception as e:
        logger.error(f"Failed to save state: {e}")


def add_error(msg: str):
    state["errors"].append({
        "message": msg,
        "time": datetime.now(timezone.utc).isoformat(),
    })
    state["errors"] = state["errors"][-50:]


# ─── Monitoring loop ───────────────────────────────────────────────────────

async def run_monitoring_cycle():
    """Main monitoring cycle - runs every 5 minutes."""
    global engine
    logger.info("Starting monitoring cycle...")

    try:
        engine = EventEngine(state)
        result = await engine.run_full_cycle()

        state["total_cycles"] += 1
        state["total_events"] = len(state["events"])
        state["total_dispatches"] = len(state["dispatches"])

        state["cycle_history"].append({
            "cycle": state["total_cycles"],
            "start": result["cycle_start"],
            "end": result["cycle_end"],
            "events": result["events_this_cycle"],
            "dispatches": result["dispatches_this_cycle"],
            "results": result["results"],
        })
        state["cycle_history"] = state["cycle_history"][-200:]

        save_state()
        logger.info(f"Cycle #{state['total_cycles']} complete: {result['events_this_cycle']} events, {result['dispatches_this_cycle']} dispatches")

    except Exception as e:
        logger.error(f"Monitoring cycle failed: {e}", exc_info=True)
        add_error(f"Monitoring cycle failed: {e}")
        save_state()


async def run_health_check():
    """Quick agent health check - runs every 2 minutes."""
    try:
        health = await check_all_agents()
        state["agent_health"] = health

        unhealthy = [a for a, h in health.items() if not h.get("healthy")]
        if unhealthy:
            logger.warning(f"Unhealthy agents: {unhealthy}")

        save_state()
    except Exception as e:
        logger.error(f"Health check failed: {e}")


# ─── App lifecycle ──────────────────────────────────────────────────────────


# ── Scheduled upgrade functions ─────────────────────────────────────

async def run_learning_update():
    """Update cross-agent learning from recent dispatches."""
    try:
        from cross_agent_learning import load_learning_data, record_dispatch_outcome, get_agent_effectiveness_scores, save_learning_data
        data = load_learning_data()
        # Record outcomes from recent dispatches
        for d in state.get("dispatches", [])[-50:]:
            record_dispatch_outcome(
                data,
                agent=d.get("agent", "unknown"),
                action=d.get("action", "unknown"),
                success=d.get("success", False),
                context={"trigger": d.get("trigger", "")},
            )
        scores = get_agent_effectiveness_scores(data)
        state["learning_data"] = {
            "scores": scores,
            "total_outcomes": len(data.get("dispatch_outcomes", [])),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        save_learning_data(data)
        save_state()
        logger.info(f"Learning update complete: {len(data.get('dispatch_outcomes', []))} outcomes tracked")
    except Exception as e:
        logger.error(f"Learning update failed: {e}")


async def run_roi_update():
    """Update ROI predictions based on dispatch history."""
    try:
        from roi_predictor import load_roi_data, record_action_outcome, save_roi_data
        data = load_roi_data()
        # Record recent dispatch outcomes
        for d in state.get("dispatches", [])[-20:]:
            record_action_outcome(
                data,
                action_type=d.get("action", "unknown"),
                predicted_roi=0.5,
                actual_roi=0.7 if d.get("success", False) else 0.1,
                context={"agent": d.get("agent", "")},
            )
        save_roi_data(data)
        state["roi_data"] = {
            "model": data.get("roi_model", {}),
            "history_count": len(data.get("action_history", [])),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        save_state()
        logger.info("ROI predictions updated")
    except Exception as e:
        logger.error(f"ROI update failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_state()
    state["started_at"] = datetime.now(timezone.utc).isoformat()

    # Main monitoring cycle every 5 minutes
    scheduler.add_job(
        run_monitoring_cycle,
        "interval",
        seconds=settings.MONITOR_INTERVAL,
        id="monitoring_cycle",
    )

    # Quick health check every 2 minutes
    scheduler.add_job(
        run_health_check,
        "interval",
        seconds=settings.HEALTH_CHECK_INTERVAL,
        id="health_check",
    )

    # Upgrade module scheduled jobs
    scheduler.add_job(run_learning_update, "interval", hours=2, id="learning_update")
    scheduler.add_job(run_roi_update, "interval", hours=4, id="roi_update")
    scheduler.start()

    # Run initial health check + cycle on startup
    await run_health_check()
    asyncio.create_task(run_monitoring_cycle())

    logger.info("Smart Manager Agent started on port %d", settings.API_PORT)
    yield
    scheduler.shutdown()


app = FastAPI(
    title="PetHub Smart Manager Agent",
    description="Central brain - monitors ecosystem 24/7 and dispatches work to child agents",
    version="1.0.0",
    lifespan=lifespan,
)


# ─── API Endpoints ──────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    now = datetime.now(timezone.utc)
    uptime = None
    if state["started_at"]:
        try:
            started = datetime.fromisoformat(state["started_at"])
            uptime = str(now - started).split(".")[0]
        except Exception:
            pass

    return {
        "agent": "manager",
        "status": "active",
        "uptime": uptime,
        "started_at": state["started_at"],
        "total_cycles": state["total_cycles"],
        "total_events": state["total_events"],
        "total_dispatches": state["total_dispatches"],
        "agent_health": {
            agent: {
                "healthy": h.get("healthy", False),
                "status": h.get("status", "unknown"),
            }
            for agent, h in state.get("agent_health", {}).items()
        },
        "recent_errors": len(state["errors"]),
        "monitoring_interval": f"{settings.MONITOR_INTERVAL}s",
    }


@app.get("/api/events")
async def get_events(limit: int = 50, severity: str = None, source: str = None):
    """Get recent events with optional filtering."""
    events = list(reversed(state["events"]))

    if severity:
        events = [e for e in events if e.get("severity") == severity]
    if source:
        events = [e for e in events if source in e.get("source", "")]

    return {
        "total": len(state["events"]),
        "showing": min(limit, len(events)),
        "events": events[:limit],
    }


@app.get("/api/dispatches")
async def get_dispatches(limit: int = 50, agent: str = None):
    """Get recent dispatches with optional agent filtering."""
    dispatches = list(reversed(state["dispatches"]))

    if agent:
        dispatches = [d for d in dispatches if d.get("agent") == agent]

    return {
        "total": len(state["dispatches"]),
        "showing": min(limit, len(dispatches)),
        "dispatches": dispatches[:limit],
    }


@app.get("/api/agents")
async def get_agent_health():
    """Get detailed health status of all agents."""
    return state.get("agent_health", {})


@app.post("/api/agents/check")
async def trigger_health_check():
    """Trigger immediate health check of all agents."""
    await run_health_check()
    return state.get("agent_health", {})


@app.post("/api/cycle/run")
async def trigger_monitoring_cycle():
    """Trigger immediate monitoring cycle."""
    asyncio.create_task(run_monitoring_cycle())
    return {"message": "Monitoring cycle triggered", "cycle": state["total_cycles"] + 1}


@app.get("/api/cycles")
async def get_cycle_history(limit: int = 20):
    """Get monitoring cycle history."""
    history = list(reversed(state["cycle_history"]))
    return {
        "total_cycles": state["total_cycles"],
        "showing": min(limit, len(history)),
        "cycles": history[:limit],
    }


@app.get("/api/snapshot/wordpress")
async def get_wp_snapshot():
    """Get latest WordPress monitoring snapshot."""
    return state.get("wp_snapshot", {})


@app.get("/api/snapshot/social")
async def get_social_snapshot():
    """Get latest social media monitoring snapshot."""
    return state.get("social_snapshot", {})


@app.get("/api/cooldowns")
async def get_cooldowns():
    """Get active cooldowns."""
    return state.get("cooldowns", {})


@app.post("/api/cooldowns/reset")
async def reset_cooldowns():
    """Reset all cooldowns (allows immediate re-dispatch)."""
    state["cooldowns"] = {}
    save_state()
    return {"message": "All cooldowns reset"}


@app.get("/api/errors")
async def get_errors():
    """Get recent errors."""
    return {"errors": state["errors"][-20:]}


# ─── Manual dispatch endpoints ─────────────────────────────────────────────

@app.post("/api/dispatch/seo/audit")
async def manual_dispatch_seo_audit():
    result = await dispatch_seo_audit()
    return result


@app.post("/api/dispatch/seo/autofix")
async def manual_dispatch_seo_autofix():
    result = await dispatch_seo_autofix()
    return result


@app.post("/api/dispatch/social/post")
async def manual_dispatch_social_post():
    result = await dispatch_social_post()
    return result


@app.post("/api/dispatch/social/reel")
async def manual_dispatch_social_reel():
    result = await dispatch_social_reel()
    return result


@app.post("/api/dispatch/maintenance/links")
async def manual_dispatch_link_scan():
    result = await dispatch_maintenance_link_scan()
    return result


@app.post("/api/dispatch/maintenance/performance")
async def manual_dispatch_perf_scan():
    result = await dispatch_maintenance_performance_scan()
    return result


@app.post("/api/dispatch/maintenance/security")
async def manual_dispatch_security_scan():
    result = await dispatch_maintenance_security_scan()
    return result


@app.post("/api/dispatch/analytics/collect")
async def manual_dispatch_analytics():
    result = await dispatch_analytics_collection()
    return result


# ─── Dashboard ─────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def manager_dashboard():
    with open("templates/manager_dashboard.html", "r") as f:
        return HTMLResponse(f.read())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=settings.API_PORT, reload=False)


# ── AI-Powered Upgrade Endpoints ─────────────────────────────────────

@app.get("/api/learning")
async def get_learning_data():
    """Get cross-agent learning data and effectiveness scores."""
    from cross_agent_learning import load_learning_data, get_agent_effectiveness_scores, get_recommended_priorities
    data = load_learning_data()
    scores = get_agent_effectiveness_scores(data)
    priorities = get_recommended_priorities(data)
    return {"agent_scores": scores, "priorities": priorities, "total_outcomes": len(data.get("dispatch_outcomes", []))}


@app.get("/api/roi")
async def get_roi_data():
    """Get ROI prediction data."""
    from roi_predictor import load_roi_data
    data = load_roi_data()
    return {"roi_model": data.get("roi_model", {}), "history_count": len(data.get("action_history", []))}


@app.post("/api/roi/predict")
async def predict_action_roi(action_type: str, urgency: float = 0.5):
    """Predict ROI for a proposed action."""
    from roi_predictor import load_roi_data, predict_roi
    data = load_roi_data()
    result = predict_roi(action_type, {"urgency": urgency}, data)
    return result

