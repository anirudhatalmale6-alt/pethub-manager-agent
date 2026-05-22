"""
Cross-agent learning loops for the Manager Agent.

Monitors outcomes of dispatched tasks and adjusts priorities based on
historical success rates and impact scores. Uses AI pattern analysis
to identify trends and suggest improvements.
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("manager.learning")

LEARNING_DATA_PATH = Path("/opt/pethub-agents/manager-agent/data/learning_data.json")

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

# Grade thresholds based on success rate
_GRADE_THRESHOLDS = {
    "A": 0.90,
    "B": 0.75,
    "C": 0.60,
    "D": 0.45,
}


def _empty_learning_data() -> dict:
    """Return a fresh learning-data structure."""
    return {
        "dispatch_outcomes": [],
        "agent_effectiveness": {},
        "action_effectiveness": {},
        "priority_adjustments": [],
    }


def load_learning_data() -> dict:
    """Load learning data from disk, or return an empty structure."""
    if not LEARNING_DATA_PATH.exists():
        return _empty_learning_data()
    try:
        raw = LEARNING_DATA_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        # Ensure all expected keys are present
        for key in ("dispatch_outcomes", "agent_effectiveness",
                     "action_effectiveness", "priority_adjustments"):
            if key not in data:
                data[key] = [] if key in ("dispatch_outcomes", "priority_adjustments") else {}
        return data
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load learning data from %s: %s", LEARNING_DATA_PATH, exc)
        return _empty_learning_data()


def save_learning_data(data: dict) -> None:
    """Persist learning data to disk."""
    try:
        LEARNING_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = LEARNING_DATA_PATH.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
        tmp_path.replace(LEARNING_DATA_PATH)
        logger.debug("Saved learning data to %s", LEARNING_DATA_PATH)
    except OSError as exc:
        logger.error("Failed to save learning data: %s", exc)


def _update_running_stats(stats: dict, success: bool, impact_score: float) -> None:
    """Update running success/failure counters and average impact in place."""
    if success:
        stats["successes"] = stats.get("successes", 0) + 1
    else:
        stats["failures"] = stats.get("failures", 0) + 1

    total = stats.get("successes", 0) + stats.get("failures", 0)
    prev_avg = stats.get("avg_impact", 0.0)
    # Incremental mean update
    stats["avg_impact"] = round(prev_avg + (impact_score - prev_avg) / total, 4)


def record_dispatch_outcome(
    agent: str,
    action: str,
    success: bool,
    impact_score: float,
    data: dict,
) -> None:
    """Record the outcome of a dispatched task.

    Args:
        agent: Name of the agent that handled the task.
        action: Action type string (e.g. "seo_audit", "link_fix").
        success: Whether the dispatch completed successfully.
        impact_score: Numeric impact score (0.0 - 1.0).
        data: The mutable learning data dict (from load_learning_data).
    """
    outcome = {
        "agent": agent,
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "impact_score": round(impact_score, 4),
    }
    data["dispatch_outcomes"].append(outcome)

    # Keep only the last 500 outcomes
    if len(data["dispatch_outcomes"]) > 500:
        data["dispatch_outcomes"] = data["dispatch_outcomes"][-500:]

    # Update agent effectiveness
    if agent not in data["agent_effectiveness"]:
        data["agent_effectiveness"][agent] = {"successes": 0, "failures": 0, "avg_impact": 0.0}
    _update_running_stats(data["agent_effectiveness"][agent], success, impact_score)

    # Update action effectiveness
    if action not in data["action_effectiveness"]:
        data["action_effectiveness"][action] = {"successes": 0, "failures": 0, "avg_impact": 0.0}
    _update_running_stats(data["action_effectiveness"][action], success, impact_score)

    logger.info(
        "Recorded outcome: agent=%s action=%s success=%s impact=%.2f",
        agent, action, success, impact_score,
    )


def _calc_grade(success_rate: float) -> str:
    """Map a success rate (0-1) to a letter grade A-F."""
    for grade, threshold in _GRADE_THRESHOLDS.items():
        if success_rate >= threshold:
            return grade
    return "F"


def get_agent_effectiveness_scores(data: dict) -> dict:
    """Get effectiveness scores for each agent.

    Returns:
        Dict keyed by agent name, each containing:
        - success_rate (float, 0-1)
        - avg_impact (float)
        - total_dispatches (int)
        - grade (str, A-F)
    """
    scores: dict = {}
    for agent, stats in data.get("agent_effectiveness", {}).items():
        total = stats.get("successes", 0) + stats.get("failures", 0)
        if total == 0:
            success_rate = 0.0
        else:
            success_rate = stats["successes"] / total

        scores[agent] = {
            "success_rate": round(success_rate, 3),
            "avg_impact": round(stats.get("avg_impact", 0.0), 3),
            "total_dispatches": total,
            "grade": _calc_grade(success_rate),
        }
    return scores


def get_recommended_priorities(data: dict) -> dict:
    """Based on learning data, recommend priority adjustments.

    Returns:
        {"adjustments": [{"agent": str, "recommendation": str, "reason": str}]}
    """
    adjustments: list[dict] = []
    agent_scores = get_agent_effectiveness_scores(data)

    for agent, score in agent_scores.items():
        total = score["total_dispatches"]
        if total < 5:
            # Not enough data to make recommendations
            continue

        success_rate = score["success_rate"]
        avg_impact = score["avg_impact"]

        if success_rate < 0.5:
            adjustments.append({
                "agent": agent,
                "recommendation": "reduce_frequency",
                "reason": (
                    f"Low success rate ({success_rate:.0%}) across {total} dispatches. "
                    f"Reduce dispatch frequency until root cause is identified."
                ),
            })
        elif success_rate >= 0.85 and avg_impact >= 0.7:
            adjustments.append({
                "agent": agent,
                "recommendation": "increase_priority",
                "reason": (
                    f"High effectiveness (success={success_rate:.0%}, impact={avg_impact:.2f}). "
                    f"Consider increasing dispatch priority for this agent."
                ),
            })

    # Check action types with consistent failures
    for action, stats in data.get("action_effectiveness", {}).items():
        total = stats.get("successes", 0) + stats.get("failures", 0)
        if total < 3:
            continue
        action_rate = stats["successes"] / total if total else 0
        if action_rate < 0.4:
            adjustments.append({
                "agent": f"action:{action}",
                "recommendation": "flag_for_review",
                "reason": (
                    f"Action '{action}' has a {action_rate:.0%} success rate "
                    f"over {total} dispatches. Needs investigation."
                ),
            })

    return {"adjustments": adjustments}


def predict_dispatch_success(agent: str, action: str, data: dict) -> float:
    """Predict likelihood of success for a proposed dispatch based on history.

    Uses a weighted combination of agent-level and action-level success rates.
    Falls back to 0.5 (neutral) when there is no historical data.

    Returns:
        Probability between 0.0 and 1.0.
    """
    agent_stats = data.get("agent_effectiveness", {}).get(agent)
    action_stats = data.get("action_effectiveness", {}).get(action)

    agent_rate: Optional[float] = None
    action_rate: Optional[float] = None

    if agent_stats:
        total = agent_stats.get("successes", 0) + agent_stats.get("failures", 0)
        if total >= 3:
            agent_rate = agent_stats["successes"] / total

    if action_stats:
        total = action_stats.get("successes", 0) + action_stats.get("failures", 0)
        if total >= 3:
            action_rate = action_stats["successes"] / total

    # Combine: if both available, weight agent 60% and action 40%
    if agent_rate is not None and action_rate is not None:
        return round(agent_rate * 0.6 + action_rate * 0.4, 3)
    if agent_rate is not None:
        return round(agent_rate, 3)
    if action_rate is not None:
        return round(action_rate, 3)

    # No historical data -- return neutral probability
    return 0.5


def _get_openai_api_key() -> str:
    """Resolve OpenAI API key from env or config."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        try:
            from config import settings  # type: ignore[import-untyped]
            api_key = settings.OPENAI_API_KEY
        except Exception:
            pass
    return api_key


async def ai_analyze_patterns(data: dict) -> Optional[dict]:
    """Use AI to find patterns in dispatch outcomes and suggest improvements.

    Summarises the last 50 dispatch outcomes and asks GPT-4o to identify
    patterns, suggest improvements, and highlight risk areas.

    Returns:
        {"patterns": [str], "suggestions": [str], "risk_areas": [str]}
        or None on failure / missing API key.
    """
    api_key = _get_openai_api_key()
    if not api_key:
        logger.warning("No OpenAI API key available -- skipping AI pattern analysis")
        return None

    outcomes = data.get("dispatch_outcomes", [])[-50:]
    if len(outcomes) < 5:
        logger.info("Too few outcomes (%d) for meaningful pattern analysis", len(outcomes))
        return None

    agent_scores = get_agent_effectiveness_scores(data)

    summary_payload = {
        "recent_outcomes": outcomes,
        "agent_scores": agent_scores,
        "action_stats": data.get("action_effectiveness", {}),
    }

    system_prompt = (
        "You are the analytics brain for Pet Hub Online (pethubonline.com), "
        "a UK-based pet supplies affiliate site with a multi-agent system. "
        "Analyze dispatch outcome data and identify actionable patterns."
    )

    user_prompt = (
        "Here is the dispatch outcome data for the PetHub agent ecosystem:\n\n"
        f"{json.dumps(summary_payload, indent=2, default=str)}\n\n"
        "Analyze this data and return a JSON object with exactly these keys:\n"
        '- "patterns": list of 2-5 observed patterns (strings)\n'
        '- "suggestions": list of 2-5 actionable improvement suggestions (strings)\n'
        '- "risk_areas": list of 1-3 areas that need attention (strings)\n\n'
        "Focus on what is actionable for a site manager. "
        "Return ONLY the JSON object, no markdown formatting."
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.4,
        "max_tokens": 600,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(OPENAI_API_URL, headers=headers, json=payload)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
    except httpx.TimeoutException:
        logger.error("OpenAI API timeout during pattern analysis")
        return None
    except httpx.HTTPStatusError as exc:
        logger.error("OpenAI API HTTP %d: %s", exc.response.status_code, exc.response.text[:300])
        return None
    except Exception as exc:
        logger.error("OpenAI API unexpected error: %s", exc)
        return None

    # Parse response, stripping markdown fences if present
    try:
        cleaned = content
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            logger.warning("AI returned non-dict for pattern analysis: %s", type(parsed))
            return None

        return {
            "patterns": parsed.get("patterns", []),
            "suggestions": parsed.get("suggestions", []),
            "risk_areas": parsed.get("risk_areas", []),
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "outcomes_analyzed": len(outcomes),
        }
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse AI pattern analysis JSON: %s", exc)
        return None
