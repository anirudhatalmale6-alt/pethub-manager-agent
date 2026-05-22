"""OpenAI GPT integration for the Manager Agent.

Uses httpx to call OpenAI API directly (no openai SDK dependency).
Provides AI-powered event prioritisation and dispatch reasoning.
"""

import json
import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger("manager.ai")

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
TIMEOUT = 15.0

SYSTEM_PROMPT = (
    "You are the orchestration brain for Pet Hub Online (pethubonline.com), "
    "a UK-based pet supplies affiliate website with a 5-agent system. "
    "You make fast, strategic decisions about which agent should handle events. "
    "Available agents: seo (SEO audits, meta tags, internal links), "
    "analytics (traffic, metrics, reports), social (Facebook/Instagram posts), "
    "maintenance (content quality, broken links, performance). "
    "You prioritise revenue impact and user experience."
)


async def _call_openai(
    messages: list[dict],
    model: str = "gpt-4o",
    temperature: float = 0.3,
    max_tokens: int = 500,
) -> Optional[str]:
    """Low-level helper to call the OpenAI chat completions endpoint."""
    headers = {
        "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(OPENAI_API_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except httpx.TimeoutException:
        logger.error("OpenAI API timeout after %.0fs", TIMEOUT)
        return None
    except httpx.HTTPStatusError as exc:
        logger.error("OpenAI API HTTP %d: %s", exc.response.status_code, exc.response.text[:300])
        return None
    except Exception as exc:
        logger.error("OpenAI API unexpected error: %s", exc)
        return None


async def ai_prioritize_actions(events: list[dict]) -> Optional[list[dict]]:
    """Re-order a list of events by AI-assessed priority.

    Args:
        events: List of event dicts, each with keys like:
            - type: event type (e.g. "new_page", "broken_link", "engagement_drop")
            - severity: "low", "medium", "high", "critical"
            - description: brief description
            - timestamp: when it was detected

    Returns:
        The same events re-ordered by priority, each with an added "priority_reason"
        field explaining the ranking, or None on failure.
    """
    events_str = json.dumps(events, indent=2, default=str)

    user_prompt = (
        "Here are detected events that need handling:\n\n"
        f"{events_str}\n\n"
        "Re-order these events by priority (most urgent first). Consider:\n"
        "- Revenue impact (broken affiliate links > cosmetic issues)\n"
        "- User experience impact (site errors > slow pages > missing meta)\n"
        "- Time sensitivity (engagement drops need fast response)\n"
        "- Cascading effects (one issue causing others)\n\n"
        "Return a JSON array with the same events re-ordered, each with an added "
        '"priority_reason" field (one short sentence explaining the ranking).\n\n'
        "Return ONLY the JSON array, no markdown formatting."
    )

    result = await _call_openai(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1000,
    )

    if result is None:
        return None

    try:
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
        logger.warning("OpenAI returned non-list for prioritised actions: %s", type(parsed))
        return None
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse prioritised actions JSON: %s", exc)
        return None


async def ai_generate_dispatch_reasoning(
    event: dict,
    available_agents: list[str],
) -> Optional[dict]:
    """Decide which agent should handle an event and explain why.

    Args:
        event: Event dict with type, severity, description, etc.
        available_agents: List of agent names currently online
            (e.g. ["seo", "social", "analytics", "maintenance"]).

    Returns:
        Dict with "agent" (str), "action" (str describing what the agent should do),
        and "reasoning" (str explaining why), or None on failure.
    """
    event_str = json.dumps(event, indent=2, default=str)

    user_prompt = (
        f"Event to handle:\n{event_str}\n\n"
        f"Available agents: {', '.join(available_agents)}\n\n"
        "Decide which agent should handle this event. Consider:\n"
        "- Which agent's capabilities best match the event type\n"
        "- Whether multiple agents need to coordinate\n"
        "- The urgency and appropriate response\n\n"
        "Return a JSON object with:\n"
        '- "agent": the primary agent to dispatch (one of the available agents)\n'
        '- "action": specific action the agent should take (be precise)\n'
        '- "reasoning": why this agent and action were chosen (1-2 sentences)\n\n'
        "Return ONLY the JSON object, no markdown formatting."
    )

    result = await _call_openai(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=300,
    )

    if result is None:
        return None

    try:
        cleaned = result.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

        parsed = json.loads(cleaned)
        if isinstance(parsed, dict) and "agent" in parsed:
            return {
                "agent": str(parsed.get("agent", "")),
                "action": str(parsed.get("action", "")),
                "reasoning": str(parsed.get("reasoning", "")),
            }
        logger.warning("OpenAI returned unexpected structure for dispatch: %s", parsed.keys() if isinstance(parsed, dict) else type(parsed))
        return None
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("Failed to parse dispatch reasoning JSON: %s", exc)
        return None
