import requests, os, json, re, logging
from typing import Any, Dict, Optional
from prompts import CATEGORY_VOICE, CATEGORY_VOICE_DEFAULT, COMPOSE_ACTION_SYSTEM, COMPOSE_REPLY_SYSTEM

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
    """Robustly parse JSON from LLM response, handling edge cases."""
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
    identity = merchant_payload.get("identity", {})
    category_name = identity.get("category", "restaurant").lower()
    category_voice = CATEGORY_VOICE.get(category_name, CATEGORY_VOICE_DEFAULT)

    context = {
        "REAL_MERCHANT_DATA": {
            "id": merchant_id,
            "name": merchant_payload.get("identity", {}).get("name", ""),
            "category": merchant_payload.get("identity", {}).get("category", ""),
            "location": merchant_payload.get("identity", {}).get("location", ""),
            "languages": merchant_payload.get("identity", {}).get("languages", ["en"]),
            "ACTUAL_PERFORMANCE_NUMBERS": merchant_payload.get("performance", {}),
            "ACTUAL_OFFERS": merchant_payload.get("offers", []),
            "leads": merchant_payload.get("leads", {}),
            "subscription": merchant_payload.get("subscription", {}),
        },
        "TRIGGER_INSIGHT": {
            "id": trigger_id,
            "type": trigger_payload.get("type", ""),
            "insight": trigger_payload.get("insight", ""),
            "category": trigger_payload.get("category", ""),
        },
        "CATEGORY_CONTEXT": category_payload,
    }
    if customer_payload:
        context["customer"] = customer_payload

    system_prompt = COMPOSE_ACTION_SYSTEM.format(
        category_voice=category_voice,
        now=now,
        context_json=json.dumps(context, ensure_ascii=False, indent=2),
    )

    try:
        user_message = (
            f"Compose the next message for merchant '{merchant_id}' "
            f"using trigger '{trigger_id}'. "
            f"Suggested suppression_key prefix: {suppression_key}"
        )
        raw = _call_llm(system_prompt, user_message)
        parsed = _parse_json_response(raw)

        if not parsed:
            logger.error(f"No valid JSON for merchant={merchant_id} trigger={trigger_id}")
            return None

        return {
            "merchant_id": merchant_id,
            "trigger_id": trigger_id,
            "body": parsed.get("body", ""),
            "cta": parsed.get("cta", "open_ended"),
            "template_name": parsed.get("template_name", "vera_generic_v1"),
            "template_params": parsed.get("template_params", []),
            "suppression_key": parsed.get("suppression_key", suppression_key),
            "send_as": parsed.get("send_as", "vera"),
            "rationale": parsed.get("rationale", ""),
        }
    except Exception as e:
        logger.error(f"OpenRouter API error in compose_action: {e}")
        return None

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
        1 for t in history[-3:]
        if t.get("role") == "vera" and t.get("action") == "wait"
    )
    if recent_waits >= 2:
        return {
            "action": "end",
            "body": "",
            "cta": "none",
            "rationale": "Loop detected — ending conversation.",
        }

    if from_role == "customer":
        if is_accept:
            for word in message.split():
                if any(c.isdigit() for c in word) or word.lower() in [
                    "morning", "evening", "afternoon", "pm", "am",
                ]:
                    break
            merchant_name = merchant_payload.get("identity", {}).get("name", "the merchant")
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
    merchant_name = merchant_payload.get("identity", {}).get("name", "this merchant")
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
