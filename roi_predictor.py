"""
ROI predictor for the Manager Agent.

Predicts the return-on-investment of dispatching specific actions to help
the manager make smarter scheduling and prioritisation decisions.
Uses historical outcomes and configurable impact weights.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("manager.roi")

ROI_DATA_PATH = Path("/opt/pethub-agents/manager-agent/data/roi_data.json")

# Base impact weights for different action types (0.0 - 1.0)
ACTION_IMPACT_WEIGHTS: dict[str, float] = {
    "seo_audit": 0.7,           # High long-term SEO value
    "seo_autofix": 0.8,         # Direct fix = high impact
    "social_post": 0.5,         # Medium engagement impact
    "social_reel": 0.6,         # Higher engagement than posts
    "link_scan": 0.6,           # Important for site health
    "link_fix": 0.9,            # Critical fix
    "performance_scan": 0.4,    # Monitoring value
    "security_scan": 0.7,       # Security is high priority
    "metadata_audit": 0.5,      # SEO maintenance
    "content_audit": 0.5,       # Content health
}

# Urgency multiplier thresholds
_URGENCY_HIGH = 0.7
_URGENCY_MEDIUM = 0.4

# Time-decay: actions not run for a long time get a boost
_STALENESS_THRESHOLD_HOURS = 48
_STALENESS_BOOST = 0.15

# Confidence labels
_CONFIDENCE_LABELS = {
    "high": 15,     # 15+ historical data points
    "medium": 5,    # 5-14 data points
    "low": 0,       # 0-4 data points
}


def _empty_roi_data() -> dict:
    """Return a fresh ROI-data structure."""
    return {
        "action_history": [],
        "roi_model": {},
    }


def load_roi_data() -> dict:
    """Load ROI data from disk, or return an empty structure."""
    if not ROI_DATA_PATH.exists():
        return _empty_roi_data()
    try:
        raw = ROI_DATA_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        for key in ("action_history", "roi_model"):
            if key not in data:
                data[key] = [] if key == "action_history" else {}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load ROI data from %s: %s", ROI_DATA_PATH, exc)
        return _empty_roi_data()


def save_roi_data(data: dict) -> None:
    """Persist ROI data to disk."""
    try:
        ROI_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = ROI_DATA_PATH.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
        tmp_path.replace(ROI_DATA_PATH)
        logger.debug("Saved ROI data to %s", ROI_DATA_PATH)
    except OSError as exc:
        logger.error("Failed to save ROI data: %s", exc)


def _get_confidence_label(data_points: int) -> str:
    """Return a confidence label based on how many data points back the prediction."""
    if data_points >= _CONFIDENCE_LABELS["high"]:
        return "high"
    if data_points >= _CONFIDENCE_LABELS["medium"]:
        return "medium"
    return "low"


def predict_roi(action_type: str, context: dict, data: dict) -> dict:
    """Predict the ROI of taking a specific action.

    Args:
        action_type: The action key (e.g. "seo_audit", "link_fix").
        context: Contextual signals that modify the prediction:
            - urgency (float 0-1): how urgent the action is
            - time_since_last (float): hours since the action was last run
            - affected_pages (int): number of pages affected
        data: The ROI data dict (from load_roi_data).

    Returns:
        {
            "predicted_roi": float (0-1),
            "confidence": str ("low"|"medium"|"high"),
            "reasoning": str (human-readable explanation),
        }
    """
    base_weight = ACTION_IMPACT_WEIGHTS.get(action_type, 0.5)
    urgency = context.get("urgency", 0.5)
    time_since_last = context.get("time_since_last", 0.0)
    affected_pages = context.get("affected_pages", 1)

    reasoning_parts: list[str] = [f"Base weight for '{action_type}': {base_weight:.2f}"]

    # --- Historical adjustment ---
    historical_roi = data.get("roi_model", {}).get(action_type)
    historical_points = 0
    if historical_roi is not None:
        # Blend base weight with historical average (50/50 when data is available)
        historical_avg = historical_roi.get("avg_roi", base_weight)
        historical_points = historical_roi.get("count", 0)
        if historical_points >= 3:
            base_weight = (base_weight + historical_avg) / 2.0
            reasoning_parts.append(
                f"Blended with historical avg {historical_avg:.2f} "
                f"({historical_points} data points)"
            )

    # --- Urgency boost ---
    urgency_boost = 0.0
    if urgency >= _URGENCY_HIGH:
        urgency_boost = 0.15
        reasoning_parts.append(f"High urgency boost (+{urgency_boost:.2f})")
    elif urgency >= _URGENCY_MEDIUM:
        urgency_boost = 0.08
        reasoning_parts.append(f"Medium urgency boost (+{urgency_boost:.2f})")

    # --- Staleness boost ---
    staleness_boost = 0.0
    if time_since_last > _STALENESS_THRESHOLD_HOURS:
        staleness_boost = _STALENESS_BOOST
        reasoning_parts.append(
            f"Stale action boost (+{staleness_boost:.2f}, "
            f"last run {time_since_last:.0f}h ago)"
        )

    # --- Page-count multiplier ---
    page_multiplier = 1.0
    if affected_pages >= 50:
        page_multiplier = 1.10
        reasoning_parts.append(f"High page count multiplier (x{page_multiplier})")
    elif affected_pages >= 20:
        page_multiplier = 1.05
        reasoning_parts.append(f"Moderate page count multiplier (x{page_multiplier})")

    # --- Combine ---
    predicted = (base_weight + urgency_boost + staleness_boost) * page_multiplier
    predicted = round(min(1.0, max(0.0, predicted)), 3)
    confidence = _get_confidence_label(historical_points)

    return {
        "predicted_roi": predicted,
        "confidence": confidence,
        "reasoning": "; ".join(reasoning_parts),
    }


def rank_pending_actions(actions: list[dict], data: dict) -> list[dict]:
    """Rank a list of pending actions by predicted ROI.

    Args:
        actions: List of action dicts, each containing:
            - type (str): action type key
            - agent (str): target agent name
            - context (dict): context dict passed to predict_roi
        data: The ROI data dict (from load_roi_data).

    Returns:
        The same list of actions sorted descending by predicted ROI,
        with added "predicted_roi", "roi_confidence", and "rank" fields.
    """
    scored: list[dict] = []
    for action in actions:
        roi_result = predict_roi(
            action_type=action.get("type", "unknown"),
            context=action.get("context", {}),
            data=data,
        )
        enriched = {**action}
        enriched["predicted_roi"] = roi_result["predicted_roi"]
        enriched["roi_confidence"] = roi_result["confidence"]
        enriched["roi_reasoning"] = roi_result["reasoning"]
        scored.append(enriched)

    # Sort by predicted_roi descending
    scored.sort(key=lambda a: a["predicted_roi"], reverse=True)

    # Assign ranks
    for idx, action in enumerate(scored, start=1):
        action["rank"] = idx

    return scored


def record_action_outcome(
    action_type: str,
    predicted_roi: float,
    actual_impact: float,
    data: dict,
) -> None:
    """Record the actual outcome to improve future predictions.

    Args:
        action_type: The action key.
        predicted_roi: What we predicted beforehand.
        actual_impact: The measured impact (0.0 - 1.0).
        data: The mutable ROI data dict.
    """
    entry = {
        "action": action_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "predicted_roi": round(predicted_roi, 4),
        "actual_impact": round(actual_impact, 4),
        "prediction_error": round(abs(predicted_roi - actual_impact), 4),
    }
    data["action_history"].append(entry)

    # Keep only the last 200 entries
    if len(data["action_history"]) > 200:
        data["action_history"] = data["action_history"][-200:]

    # Update roi_model with running average for this action type
    model = data.get("roi_model", {})
    if action_type not in model:
        model[action_type] = {"avg_roi": actual_impact, "count": 1}
    else:
        prev = model[action_type]
        count = prev.get("count", 0) + 1
        prev_avg = prev.get("avg_roi", 0.0)
        new_avg = prev_avg + (actual_impact - prev_avg) / count
        model[action_type] = {"avg_roi": round(new_avg, 4), "count": count}

    data["roi_model"] = model

    logger.info(
        "Recorded ROI outcome: action=%s predicted=%.3f actual=%.3f error=%.3f",
        action_type, predicted_roi, actual_impact, entry["prediction_error"],
    )
