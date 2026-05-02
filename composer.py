import requests, os, json, re, logging
from typing import Any, Dict, Optional
from prompts import CATEGORY_VOICE, CATEGORY_VOICE_DEFAULT, COMPOSE_ACTION_SYSTEM, COMPOSE_REPLY_SYSTEM

logger = logging.getLogger(__name__)
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "meta-llama/llama-3.3-70b-instruct"
API_URL = "https://openrouter.ai/api/v1/chat/completions"

def _call_llm(system_prompt: str, user_message: str) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "max_tokens": 512,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    print("=== DEBUG SYSTEM PROMPT (first 300 chars) ===")
    print(system_prompt[:300])
    print("=== DEBUG USER MESSAGE ===")
    print(user_message)
    print("=== END DEBUG ===")
    resp = requests.post(API_URL, headers=headers, json=payload, timeout=25)
    resp.raise_for_status()
    raw_text = resp.json()["choices"][0]["message"]["content"]
    print("=== DEBUG RAW RESPONSE ===")
    print(raw_text)
    print("=== END DEBUG ===")
    return raw_text

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
        "merchant": {
            "id": merchant_id,
            "identity": identity,
            "performance": merchant_payload.get("performance", {}),
            "offers": merchant_payload.get("offers", []),
            "conversation_history_summary": merchant_payload.get("conversation_history_summary", "none"),
        },
        "trigger": {
            "id": trigger_id,
            **trigger_payload,
        },
        "category_guidelines": category_payload,
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
    history_lines = []
    for turn in history:
        role = turn.get("role", "merchant")
        msg = turn.get("message", "")
        t = turn.get("turn", "?")
        history_lines.append(f"Turn {t} [{role}]: {msg}")
    history_text = "\n".join(history_lines) if history_lines else "No prior history."

    system_prompt = COMPOSE_REPLY_SYSTEM.format(
        merchant_json=json.dumps(merchant_payload, ensure_ascii=False, indent=2),
        history_text=history_text,
    )

    try:
        user_message = (
            f"The merchant just said (turn {turn_number}): \"{message}\"\n"
            f"What should Vera do next?"
        )
        raw = _call_llm(system_prompt, user_message)
        parsed = _parse_json_response(raw)

        if not parsed:
            logger.error(f"No valid JSON for reply conv={conversation_id}")
            return {
                "action": "wait",
                "body": "",
                "rationale": "Parse failure — waiting for retry.",
            }

        action = parsed.get("action", "send")
        if action not in ("send", "wait", "end"):
            action = "send"

        return {
            "action": action,
            "body": parsed.get("body", ""),
            "cta": parsed.get("cta", "open_ended"),
            "rationale": parsed.get("rationale", ""),
        }
    except Exception as e:
        logger.error(f"OpenRouter API error in compose_reply: {e}")
        return {"action": "wait", "body": "", "rationale": f"API error: {str(e)}"}
