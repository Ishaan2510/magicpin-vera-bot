"""
Vera Message Engine — Magicpin AI Challenge
Main Flask application exposing the 5 required endpoints.
"""

import time
import uuid
import logging
from datetime import datetime, timezone
from flask import Flask, request, jsonify

from storage import ContextStore
from composer import compose_action, compose_reply

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
store = ContextStore()
START_TIME = time.time()


# ---------------------------------------------------------------------------
# GET /v1/healthz
# ---------------------------------------------------------------------------
@app.route("/v1/healthz", methods=["GET"])
def healthz():
    counts = store.context_counts()
    return jsonify({
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": counts,
    }), 200


# ---------------------------------------------------------------------------
# GET /v1/metadata
# ---------------------------------------------------------------------------
@app.route("/v1/metadata", methods=["GET"])
def metadata():
    return jsonify({
        "team_name": "Ishaan Goswami",
        "team_members": ["Ishaan Goswami"],
        "model": "llama-3.3-70b-versatile (groq/openrouter/together fallback)",
        "approach": "context-grounded single-signal composer with category-aware prompting and suppression",
        "contact_email": "ishaangoswami735@gmail.com",
        "version": "1.0.0",
        "submitted_at": "2026-05-02T00:00:00Z",
    }), 200


# ---------------------------------------------------------------------------
# POST /v1/context
# ---------------------------------------------------------------------------
@app.route("/v1/context", methods=["POST"])
def context():
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error": "invalid JSON"}), 400

    scope = data.get("scope")
    context_id = data.get("context_id")
    version = data.get("version", 1)
    payload = data.get("payload", {})
    delivered_at = data.get("delivered_at", datetime.now(timezone.utc).isoformat())

    if scope not in ("category", "merchant", "customer", "trigger"):
        return jsonify({"error": f"unknown scope: {scope}"}), 400
    if not context_id:
        return jsonify({"error": "context_id required"}), 400

    with store._lock:
        existing = store._store[scope].get(context_id)
        if existing and existing.get("version", 0) >= version:
            return jsonify({"accepted": False, "reason": "stale_version", "current_version": existing["version"]}), 409
        store._store[scope][context_id] = {"version": version, "payload": payload}
    ack_id = f"ack_{uuid.uuid4().hex[:8]}"
    stored_at = datetime.now(timezone.utc).isoformat()
    return jsonify({"accepted": True, "ack_id": ack_id, "stored_at": stored_at}), 200


# ---------------------------------------------------------------------------
# POST /v1/tick
# ---------------------------------------------------------------------------
@app.route("/v1/tick", methods=["POST"])
def tick():
    data = request.get_json(force=True, silent=True) or {}
    now = data.get("now", datetime.now(timezone.utc).isoformat())
    available_triggers = data.get("available_triggers", [])

    merchants = store.get_all("merchant")
    categories = store.get_all("category")
    triggers = store.get_all("trigger")

    if not merchants:
        return jsonify({"actions": []}), 200

    # Build candidate pairs: (merchant_id, trigger_id, priority_score)
    candidates = []
    for merchant_id, merchant_ctx in merchants.items():
        payload = merchant_ctx["payload"]
        identity = payload.get("identity", {})
        category = identity.get("category", "").lower()

        for trigger_id in available_triggers:
            # Check suppression
            trigger_ctx = triggers.get(trigger_id, {})
            trigger_payload = trigger_ctx.get("payload", {})

            # Safely determine trigger type for suppression key
            # Priority: payload.kind -> trigger_id split -> default 'generic'
            trigger_type = trigger_payload.get("kind") or trigger_id.split("_")[1] if "_" in trigger_id else "generic"

            # Build suppression key
            week = datetime.now().strftime("%Y-W%W")
            suppression_key = f"{trigger_type}:{category}:{week}"

            if store.is_suppressed(merchant_id, suppression_key):
                continue

            # Priority: prefer triggers that match the merchant's category
            trigger_category = trigger_payload.get("category", "").lower()
            priority = 10 if trigger_category == category else 5

            # Boost if merchant has a performance signal
            performance = payload.get("performance", {})
            if performance.get("footfall_trend") == "dip" or performance.get("ctr", 1) < performance.get("peer_median_ctr", 1):
                priority += 3

            candidates.append((merchant_id, trigger_id, priority, suppression_key))

    # Sort by priority, take top 20
    candidates.sort(key=lambda x: x[2], reverse=True)
    candidates = candidates[:20]

    if not candidates:
        return jsonify({"actions": []}), 200

    # Compose actions (run concurrently for speed)
    import concurrent.futures
    actions = []

    def compose_one(merchant_id, trigger_id, priority, suppression_key):
        merchant_ctx = merchants.get(merchant_id, {})
        trigger_ctx = triggers.get(trigger_id, {})
        category_name = merchant_ctx.get("payload", {}).get("identity", {}).get("category", "restaurant")
        category_ctx = categories.get(category_name, categories.get(f"cat_{category_name}", {}))

        # Resolve customer if trigger is customer-scoped
        trigger_payload = trigger_ctx.get("payload", {})
        customer_id = trigger_payload.get("customer_id")
        customer_payload = None
        if customer_id:
            customer_ctx = store.get("customer", customer_id)
            customer_payload = customer_ctx.get("payload") if customer_ctx else None

        result = compose_action(
            merchant_id=merchant_id,
            merchant_payload=merchant_ctx.get("payload", {}),
            trigger_id=trigger_id,
            trigger_payload=trigger_payload,
            category_payload=category_ctx.get("payload", {}),
            customer_payload=customer_payload,
            suppression_key=suppression_key,
            now=now,
        )
        if result:
            # Add conversation_id and customer_id to the final action
            result["conversation_id"] = f"conv_{merchant_id}_{trigger_id}_{uuid.uuid4().hex[:6]}"
            result["customer_id"] = None
            result["template_name"] = "vera_composed_v1"
            result["template_params"] = []
            store.add_suppression(merchant_id, suppression_key)
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(compose_one, *c): c for c in candidates}
        for future in concurrent.futures.as_completed(futures, timeout=25):
            try:
                result = future.result()
                if result:
                    actions.append(result)
            except Exception as e:
                logger.error(f"compose error: {e}")

    logger.info(f"tick: {len(candidates)} candidates → {len(actions)} actions")
    return jsonify({"actions": actions}), 200


# ---------------------------------------------------------------------------
# POST /v1/reply
# ---------------------------------------------------------------------------
@app.route("/v1/reply", methods=["POST"])
def reply():
    data = request.get_json(force=True, silent=True) or {}
    conversation_id = data.get("conversation_id", f"conv_{uuid.uuid4().hex[:8]}")
    merchant_id = data.get("merchant_id", "")
    customer_id = data.get("customer_id")
    from_role = data.get("from_role", "merchant")
    message = data.get("message", "")
    received_at = data.get("received_at")
    turn_number = data.get("turn_number", 1)

    # Load conversation history
    history = store.get_conversation(conversation_id)
    history.append({
        "role": from_role,
        "message": message,
        "turn": turn_number,
        "received_at": received_at
    })
    store.set_conversation(conversation_id, history)

    # Load merchant context for grounding
    merchant_ctx = store.get("merchant", merchant_id) or {}
    merchant_payload = merchant_ctx.get("payload", {})

    result = compose_reply(
        conversation_id=conversation_id,
        merchant_id=merchant_id,
        merchant_payload=merchant_payload,
        history=history,
        from_role=from_role,
        message=message,
        turn_number=turn_number,
    )

    logger.info(f"reply: conv={conversation_id} turn={turn_number} action={result.get('action')}")
    return jsonify({
        "action": result.get("action", "wait"),
        "body": result.get("body", ""),
        "cta": result.get("cta", "open_ended"),
        "rationale": result.get("rationale", ""),
    }), 200


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
