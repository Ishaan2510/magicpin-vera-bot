import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, Optional

import requests

from prompts import (
    CATEGORY_VOICE,
    CATEGORY_VOICE_DEFAULT,
    COMPOSE_ACTION_SYSTEM,
    COMPOSE_REPLY_SYSTEM,
)

logger = logging.getLogger(__name__)

PROVIDERS = [
    {
        "name": "Groq",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "key_env": "GROQ_API_KEY",
        "model": "llama-3.3-70b-versatile",
    },
    {
        "name": "OpenRouter-Free",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "key_env": "OPENROUTER_API_KEY",
        "model": None,
        "models": [
            "meta-llama/llama-3.3-70b-instruct:free",
            "mistralai/mistral-7b-instruct:free",
            "google/gemma-3-27b-it:free",
            "deepseek/deepseek-r1:free",
        ],
    },
]


def _call_llm(system_prompt: str, user_message: str) -> str:
    last_error = None
    for provider in PROVIDERS:
        api_key = os.environ.get(provider["key_env"], "")
        if not api_key:
            logger.info(f"Skipping {provider['name']} — no key set")
            continue
        try:
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "max_tokens": 512,
                "temperature": 0.3,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            }
            if provider.get("models"):
                payload["models"] = provider["models"]
            else:
                payload["model"] = provider["model"]

            resp = requests.post(
                provider["url"],
                headers=headers,
                json=payload,
                timeout=25,
            )
            if resp.status_code in (429, 402):
                logger.warning(f"{provider['name']} rate limited ({resp.status_code}) — trying next")
                last_error = f"{provider['name']}: HTTP {resp.status_code}"
                continue
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            logger.info(f"Composed via {provider['name']}")
            return content
        except requests.exceptions.Timeout:
            logger.warning(f"{provider['name']} timed out — trying next")
            last_error = f"{provider['name']}: timeout"
            continue
        except Exception as e:
            logger.warning(f"{provider['name']} failed: {e} — trying next")
            last_error = str(e)
            continue
    raise RuntimeError(f"All providers failed. Last error: {last_error}")


def _parse_json_response(text: str) -> Optional[Dict]:
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    logger.warning(f"Failed to parse JSON from: {text[:200]}")
    return None


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def build_composition_brief(
    merchant_id,
    merchant_payload,
    trigger_id,
    trigger_payload,
    category_payload,
    now,
):
    """
    Extract verified facts, select one signal, and choose urgency before the LLM writes.
    This function only uses fields that exist in the merchant or trigger payload.
    """
    identity = merchant_payload.get("identity", {})
    performance = merchant_payload.get("performance", {})
    offers = merchant_payload.get("offers", [])
    leads = merchant_payload.get("leads", {})
    subscription = merchant_payload.get("subscription", {})
    reviews = merchant_payload.get("reviews", {})
    calls = merchant_payload.get("calls", {})

    merchant_name = identity.get("name", merchant_id)
    category = identity.get("category", "restaurant").lower()
    location = identity.get("location", "")
    languages = identity.get("languages", ["en"])
    use_hindi = any(l in ("hi", "hi-en") for l in languages)

    verified_facts: Dict[str, Any] = {}

    ctr_raw = performance.get("ctr")
    peer_ctr_raw = performance.get("peer_median_ctr")
    ctr = _to_float(ctr_raw)
    peer_ctr = _to_float(peer_ctr_raw)
    if ctr_raw is not None:
        verified_facts["ctr"] = f"{ctr_raw}%"
    if peer_ctr_raw is not None:
        verified_facts["peer_ctr"] = f"{peer_ctr_raw}%"
    if ctr is not None and peer_ctr is not None and ctr < peer_ctr:
        gap = round(peer_ctr - ctr, 1)
        verified_facts["ctr_gap"] = f"{gap}% below peer median"

    footfall = performance.get("footfall_trend")
    if footfall:
        verified_facts["footfall_trend"] = footfall

    if offers:
        best_offer = offers[0]
        offer_name = best_offer.get("name", "")
        offer_price = best_offer.get("price", "")
        verified_facts["offer_name"] = offer_name
        verified_facts["offer_price"] = f"₹{offer_price}"
        verified_facts["offer_full"] = f"{offer_name} @ ₹{offer_price}"

    uncontacted = leads.get("uncontacted") or leads.get("new") or leads.get("count")
    uncontacted_int = _to_int(uncontacted)
    if uncontacted_int is not None:
        verified_facts["uncontacted_leads"] = uncontacted_int

    calls_30d = calls.get("last_30_days") or calls.get("count")
    calls_30d_int = _to_int(calls_30d)
    if calls_30d_int is not None:
        verified_facts["calls_30d"] = calls_30d_int

    review_count = reviews.get("count") or reviews.get("total")
    review_rating = reviews.get("rating") or reviews.get("avg")
    review_count_int = _to_int(review_count)
    review_rating_float = _to_float(review_rating)
    if review_count_int is not None:
        verified_facts["review_count"] = review_count_int
    if review_rating_float is not None:
        verified_facts["review_rating"] = review_rating_float

    sub_days = subscription.get("days_remaining") or subscription.get("expires_in_days")
    sub_days_int = _to_int(sub_days)
    if sub_days_int is not None:
        verified_facts["subscription_days_remaining"] = sub_days_int

    for k, v in performance.items():
        if k not in ("ctr", "peer_median_ctr", "footfall_trend") and isinstance(v, (int, float)):
            verified_facts[f"perf_{k}"] = v

    if footfall == "dip" or (ctr is not None and peer_ctr is not None and ctr < peer_ctr):
        urgency_lever = "loss_aversion"
        if "ctr_gap" in verified_facts:
            urgency_context = f"CTR is {verified_facts['ctr_gap']} — leads being lost right now"
        else:
            urgency_context = "footfall is dipping — act before the window closes"
    elif uncontacted_int and uncontacted_int > 0:
        urgency_lever = "effort_externalization"
        urgency_context = f"{uncontacted_int} leads are waiting — I have the message drafted, just say go"
    elif sub_days_int and sub_days_int <= 7:
        urgency_lever = "time_window"
        urgency_context = f"subscription expires in {sub_days_int} days"
    elif trigger_payload.get("type") in ("seasonal", "regulation_change", "research"):
        urgency_lever = "time_window"
        urgency_context = "time-sensitive window — this trigger is active now"
    else:
        urgency_lever = "social_proof"
        urgency_context = f"similar merchants in {location} are acting on this now"

    trigger_type = trigger_payload.get("type", "generic")
    trigger_insight = trigger_payload.get("insight", "")
    trigger_category = trigger_payload.get("category", "")

    if "uncontacted_leads" in verified_facts:
        anchor_fact = f"{verified_facts['uncontacted_leads']} uncontacted leads"
    elif "ctr_gap" in verified_facts:
        anchor_fact = f"CTR {verified_facts['ctr']}, peer median {verified_facts['peer_ctr']}"
    elif "calls_30d" in verified_facts:
        anchor_fact = f"{verified_facts['calls_30d']} calls in 30 days"
    elif "offer_full" in verified_facts:
        anchor_fact = verified_facts["offer_full"]
    elif trigger_insight:
        anchor_fact = trigger_insight[:100]
    else:
        anchor_fact = f"performance data for {merchant_name}"

    return {
        "merchant_id": merchant_id,
        "merchant_name": merchant_name,
        "category": category,
        "location": location,
        "use_hindi": use_hindi,
        "verified_facts": verified_facts,
        "urgency_lever": urgency_lever,
        "urgency_context": urgency_context,
        "anchor_fact": anchor_fact,
        "trigger_type": trigger_type,
        "trigger_insight": trigger_insight,
        "trigger_category": trigger_category,
        "offer_full": verified_facts.get("offer_full", ""),
        "category_voice": category_payload,
    }


def _build_fallback_body(brief):
    """Pure Python message construction used when the LLM fails."""
    name = brief["merchant_name"]
    facts = brief["verified_facts"]
    urgency = brief["urgency_context"]

    if brief["offer_full"] and facts.get("uncontacted_leads"):
        return f"{name}, {facts['uncontacted_leads']} leads waiting. Push {brief['offer_full']} to them now?"[:220]
    if brief["offer_full"] and facts.get("ctr_gap"):
        return f"{name}, CTR is {facts['ctr_gap']}. {brief['offer_full']} could close the gap. Want me to send it?"[:220]
    if facts.get("uncontacted_leads"):
        return f"{name}, {facts['uncontacted_leads']} new leads in your area. Want me to reach out to them now?"[:220]
    if facts.get("ctr_gap") and brief["offer_full"]:
        return f"{name}, {facts['ctr_gap']}. I've drafted a push for {brief['offer_full']} — say yes to send."[:220]
    if brief["offer_full"]:
        return f"{name}, {urgency}. Shall I push {brief['offer_full']} to nearby customers today?"[:220]
    return f"{name}, {urgency}. Want me to put together a targeted push for this week?"[:220]


def _body_uses_only_verified_numbers(body: str, brief: Dict[str, Any]) -> bool:
    numbers = re.findall(r"\d+(?:\.\d+)?", body)
    if not numbers:
        return True

    allowed_text = json.dumps(brief["verified_facts"], ensure_ascii=False)
    if "calls_30d" in brief["verified_facts"]:
        allowed_text += " 30"

    return all(number in allowed_text for number in numbers)


def _body_has_clear_cta(body: str) -> bool:
    body_lower = body.lower()
    return any(token in body_lower for token in ("?", "go?", "bhejun", "say yes", "want me", "shall i"))


def compose_action(
    merchant_id: str,
    merchant_payload: Dict[str, Any],
    trigger_id: str,
    trigger_payload: Dict[str, Any],
    category_payload: Dict[str, Any],
    customer_payload: Optional[Dict[str, Any]] = None,
    suppression_key: str = "",
    now: str = "",
) -> Optional[Dict]:
    brief = build_composition_brief(
        merchant_id, merchant_payload, trigger_id, trigger_payload, category_payload, now
    )
    category_voice = CATEGORY_VOICE.get(brief["category"], CATEGORY_VOICE_DEFAULT)

    try:
        dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except Exception:
        dt = datetime.utcnow()
    week_str = dt.strftime("%Y-W%W")
    computed_suppression = f"{brief['trigger_type']}:{brief['category']}:{week_str}"

    system_prompt = COMPOSE_ACTION_SYSTEM.format(
        category_voice=category_voice,
        now=now,
        merchant_name=brief["merchant_name"],
        category=brief["category"],
        location=brief["location"],
        anchor_fact=brief["anchor_fact"],
        verified_facts_json=json.dumps(brief["verified_facts"], ensure_ascii=False),
        urgency_lever=brief["urgency_lever"],
        urgency_context=brief["urgency_context"],
        offer_full=brief["offer_full"] or "no active offer",
        trigger_type=brief["trigger_type"],
        trigger_insight=brief["trigger_insight"] or "no additional insight",
        use_hindi="yes" if brief["use_hindi"] else "no",
        suppression_key=computed_suppression,
    )

    user_msg = (
        f"Write the Vera message for {brief['merchant_name']} "
        f"using the {brief['trigger_type']} trigger. "
        f"Primary anchor: {brief['anchor_fact']}. "
        f"Urgency lever to use: {brief['urgency_lever']} — {brief['urgency_context']}."
    )

    try:
        raw = _call_llm(system_prompt, user_msg)
        parsed = _parse_json_response(raw)
        if (
            not parsed
            or not parsed.get("body")
            or not _body_uses_only_verified_numbers(parsed.get("body", ""), brief)
            or not _body_has_clear_cta(parsed.get("body", ""))
        ):
            body = _build_fallback_body(brief)
            return {
                "merchant_id": merchant_id,
                "trigger_id": trigger_id,
                "body": body,
                "cta": "yes_no",
                "template_name": "vera_fallback_v1",
                "template_params": [brief["merchant_name"], brief["anchor_fact"]],
                "suppression_key": computed_suppression,
                "send_as": "vera",
                "rationale": f"Fallback: {brief['urgency_lever']} on {brief['anchor_fact']}",
            }

        cta = parsed.get("cta", "yes_no")
        if cta not in ("yes_no", "open_ended", "confirm", "choose", "none"):
            cta = "yes_no"

        return {
            "merchant_id": merchant_id,
            "trigger_id": trigger_id,
            "body": parsed.get("body", "")[:220],
            "cta": cta,
            "template_name": parsed.get("template_name", "vera_composed_v1"),
            "template_params": parsed.get("template_params", []),
            "suppression_key": parsed.get("suppression_key", computed_suppression),
            "send_as": parsed.get("send_as", "vera"),
            "rationale": parsed.get("rationale", ""),
        }
    except Exception as e:
        logger.error(f"compose_action error: {e}")
        body = _build_fallback_body(brief)
        return {
            "merchant_id": merchant_id,
            "trigger_id": trigger_id,
            "body": body,
            "cta": "yes_no",
            "template_name": "vera_fallback_v1",
            "template_params": [],
            "suppression_key": computed_suppression,
            "send_as": "vera",
            "rationale": f"Exception fallback: {str(e)[:100]}",
        }


def compose_reply(
    conversation_id: str,
    merchant_id: str,
    merchant_payload: Dict[str, Any],
    history: list,
    from_role: str,
    message: str,
    turn_number: int,
) -> Dict:
    msg_lower = message.lower().strip()

    ACCEPT_SIGNALS = [
        "yes", "haan", "ha", "ok", "okay", "sure", "go ahead",
        "send it", "proceed", "book", "confirm", "let's do",
        "please book", "wed", "thu", "mon", "tue", "fri", "sat", "sun",
        "5 nov", "6pm", "7pm", "8pm", "morning", "evening", "afternoon",
        "bilkul", "kar do", "bhejo", "schedule",
    ]
    REJECT_SIGNALS = [
        "no", "nahi", "nope", "not interested", "band karo",
        "stop", "mat bhejo", "cancel", "don't", "dont",
    ]
    AUTO_REPLY_SIGNALS = [
        "thank you for contacting", "we will get back",
        "out of office", "auto", "automated", "away",
    ]

    is_accept = any(s in msg_lower for s in ACCEPT_SIGNALS)
    is_reject = any(s in msg_lower for s in REJECT_SIGNALS)
    is_auto = any(s in msg_lower for s in AUTO_REPLY_SIGNALS)

    recent_waits = sum(
        1 for t in history[-5:]
        if t.get("role") == "vera" and t.get("action") == "wait"
    )
    if recent_waits >= 2:
        return {
            "action": "end",
            "body": "",
            "cta": "none",
            "rationale": "Loop detected — ending conversation.",
        }

    merchant_name = (
        merchant_payload.get("identity", {}).get("name") or
        merchant_id.replace("_", " ").title() or
        "your merchant"
    )

    if from_role == "customer":
        if is_accept:
            body = f"Booking confirmed! {merchant_name} will see you then. You'll get a reminder 1hr before."
            return {
                "action": "send",
                "body": body,
                "cta": "none",
                "rationale": "Customer confirmed slot — booking acknowledged.",
            }
        return {
            "action": "end",
            "body": "No problem! Reach out anytime.",
            "cta": "none",
            "rationale": "Customer declined.",
        }

    if is_auto:
        return {
            "action": "wait",
            "body": "",
            "cta": "none",
            "rationale": "Auto-reply detected.",
        }

    if is_accept and not is_reject:
        offers = merchant_payload.get("offers", [])
        offer_text = ""
        if offers:
            o = offers[0]
            offer_text = f" — starting with {o.get('name', '')} @ ₹{o.get('price', '')}"
        body = f"Perfect{offer_text}. Sending now — you'll see results within 24hrs. Need anything else?"
        return {
            "action": "send",
            "body": body[:200],
            "cta": "yes_no",
            "rationale": "Merchant accepted — confirming and advancing.",
        }

    if is_reject:
        return {
            "action": "end",
            "body": "Understood! I'll check back when the timing is better.",
            "cta": "none",
            "rationale": "Merchant declined.",
        }

    history_text = "\n".join([
        f"Turn {t.get('turn','?')} [{t.get('role','?')}]: {t.get('message','')}"
        for t in history[-4:]
    ])
    offers = merchant_payload.get("offers", [])
    offer_str = ", ".join([f"{o.get('name')} @ ₹{o.get('price')}" for o in offers[:2]])

    system = f"""You are Vera, magicpin's merchant assistant. Reply to this merchant message.
Merchant: {merchant_name}
Available offers: {offer_str if offer_str else 'none'}
Recent conversation:
{history_text}

Rules:
- If it's a question, answer it using only the merchant info above
- Keep reply under 150 characters
- Output strict JSON only: {{"action": "send", "body": "...", "cta": "open_ended", "rationale": "..."}}
- action must be send, wait, or end"""

    user = f'Merchant said: "{message}". What should Vera reply?'

    try:
        raw = _call_llm(system, user)
        parsed = _parse_json_response(raw)
        if not parsed or not parsed.get("body"):
            return {
                "action": "send",
                "body": "Got it! Want me to help with anything specific for your business?",
                "cta": "open_ended",
                "rationale": "LLM fallback.",
            }
        action = parsed.get("action", "send")
        if action not in ("send", "wait", "end"):
            action = "send"
        return {
            "action": action,
            "body": parsed.get("body", "")[:200],
            "cta": parsed.get("cta", "open_ended"),
            "rationale": parsed.get("rationale", ""),
        }
    except Exception as e:
        logger.error(f"compose_reply LLM error: {e}")
        return {
            "action": "send",
            "body": "Got it! Let me know how I can help grow your business.",
            "cta": "open_ended",
            "rationale": "Exception fallback.",
        }
