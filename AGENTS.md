# AGENTS.md — Magicpin AI Challenge: Vera Message Engine

## Project Mission

Build a deployed HTTP bot that receives merchant/category/trigger/customer context and composes
high-compulsion, specific, grounded business messages for Magicpin's Vera AI assistant.

A LLM-powered judge evaluates output on five dimensions (each 0–10):
- Decision quality — pick the single best signal, not all facts
- Specificity — real numbers, offers, dates, local facts from the given input
- Category fit — dentists are clinical, salons are visual, restaurants are timely, gyms motivational, pharmacies utility-first
- Merchant fit — personalize to their metrics, offer catalog, conversation history
- Engagement compulsion — one clear reason to reply NOW, low-friction CTA

**Generic messages lose. Grounded, specific, one-CTA messages win.**

---

## Architecture

```
magicpin_ai/
├── AGENTS.md           ← this file
├── app.py              ← Flask app, all 5 endpoints
├── composer.py         ← compose() function, calls Anthropic API
├── storage.py          ← in-memory context store (thread-safe)
├── prompts.py          ← prompt templates, category voice guidelines
├── requirements.txt
├── Procfile            ← for Railway deployment
├── railway.toml        ← Railway config
└── README.md           ← submission README
```

---

## The 5 Required Endpoints

### GET /v1/healthz
Liveness probe. Three consecutive failures disqualify the run.

**Response 200:**
```json
{
  "status": "ok",
  "uptime_seconds": 3600,
  "contexts_loaded": {
    "category": 5,
    "merchant": 50,
    "customer": 200,
    "trigger": 100
  }
}
```

### GET /v1/metadata
Team identity for leaderboard.

**Response 200:**
```json
{
  "team_name": "Ishaan Goswami",
  "team_members": ["Ishaan Goswami"],
  "model": "llama-3.3-70b-versatile",
  "approach": "context-grounded single-signal composer with category-aware prompting",
  "version": "1.0.0"
}
```

### POST /v1/context
Receives and stores context. Idempotent by scope + context_id + version. Higher version replaces atomically.

**Request body:**
```json
{
  "scope": "merchant",
  "context_id": "m_001_drmeera",
  "version": 3,
  "payload": {
    "identity": {"name": "Dr. Meera", "category": "dentist", "location": "South Delhi"},
    "performance": {"ctr": 2.1, "peer_median_ctr": 3.0, "footfall_trend": "dip"},
    "offers": [{"name": "Dental Cleaning", "price": 299}]
  },
  "delivered_at": "2026-04-29T10:00:00Z"
}
```

Scopes can be: `"category"`, `"merchant"`, `"customer"`, `"trigger"`

**Response 200:**
```json
{
  "accepted": true,
  "ack_id": "ack_abc123",
  "stored_at": "2026-04-29T10:00:00.123Z"
}
```

### POST /v1/tick
Periodic wake-up. Bot decides which merchants to message and what to say.
Called every ~5 minutes during the 60-minute test window.
Return ≤ 20 actions per tick.

**Request:**
```json
{
  "now": "2026-04-29T10:30:00Z",
  "available_triggers": ["trg_research_digest_dentists"]
}
```

**Response 200:**
```json
{
  "actions": [
    {
      "merchant_id": "m_001_drmeera",
      "trigger_id": "trg_research_digest_dentists",
      "body": "190 people in your locality are searching for 'Dental Check Up'. Should I send them a discounted check up at ₹299?",
      "cta": "open_ended",
      "suppression_key": "research:dentists:2026-W17",
      "send_as": "vera",
      "rationale": "CTR is 0.9% below peer median; research digest provides urgency hook; ₹299 offer closes the loop."
    }
  ]
}
```

**CTA types:** `"yes_no"`, `"open_ended"`, `"confirm"`, `"choose"`, `"none"`

**suppression_key format:** `"{trigger_type}:{category}:{time_window}"` — prevents duplicate sends.

### POST /v1/reply
A merchant or customer replied. Bot must respond within 30s.

**Request:**
```json
{
  "conversation_id": "conv_001",
  "merchant_id": "m_001_drmeera",
  "from_role": "merchant",
  "message": "Yes, send me the abstract",
  "turn_number": 2
}
```

**Response 200:**
```json
{
  "action": "send",
  "body": "Sending now — also drafted a 90-sec patient WhatsApp message around your Dental Cleaning offer...",
  "rationale": "Honoring accept; adding low-friction next step"
}
```

Action can be `"send"`, `"wait"`, or `"end"`.

---

## Core Implementation Logic

### storage.py
Use a Python dict with a threading.Lock for thread-safe context storage.

```python
# Structure:
store = {
  "category": {},   # context_id -> {version, payload}
  "merchant": {},   # context_id -> {version, payload}
  "customer": {},   # context_id -> {version, payload}
  "trigger": {},    # context_id -> {version, payload}
}
# Also maintain: conversation_history = {}  # conv_id -> [turns]
# And: suppression_keys = set()  # to avoid duplicate sends
```

The `/v1/context` endpoint must:
1. Check if context_id already exists with same or higher version → if so, no-op (return accepted: true)
2. If new or higher version → atomically replace

### composer.py — The Core

The `compose()` function is the heart of the bot. It should:

1. **Select the single best signal**: From available triggers, find the one that matches the most merchants with the most compelling combination of trigger + merchant state.

2. **Build a rich context block**: Pull merchant identity, performance numbers, offers, and category voice guidelines into a structured prompt.

3. **Call Codex API** with a system prompt that enforces:
   - Use ONLY facts from the provided context (no hallucination)
   - Output a single JSON object with: body, cta, suppression_key, send_as, rationale
   - Body must contain at least one specific number or named offer from the context
   - Rationale must name the signal used and why it was chosen
   - One CTA only

4. **Parse and return** the structured output.

### prompts.py — Category Voice

Define CATEGORY_VOICE dict:
```python
CATEGORY_VOICE = {
  "dentist": "Clinical, trust-building. Lead with health outcome. Avoid pushy sales language. Use patient-count data.",
  "salon": "Visual, aspirational. Lead with look/occasion. Use booking scarcity. Seasonal angle works well.",
  "restaurant": "Timely, appetite-driven. Lead with what's hot or live event. Use footfall or order data.",
  "gym": "Motivational, outcome-focused. Lead with progress metric. Reframe dips as opportunity.",
  "pharmacy": "Utility-first, compliance-led. Lead with refill window or health alert. Clinical tone.",
}
```

---

## Prompt Strategy (Critical for Winning)

The system prompt for the composer LLM call must say:

```
You are Vera, magicpin's AI assistant for merchant growth.
Your job is to compose one message that compels a merchant to take action right now.

RULES:
1. Use ONLY facts from the context below. Never invent numbers, names, or claims.
2. Pick ONE signal (trigger + merchant metric + category insight) and build around it.
3. Include at least one specific number (CTR %, offer price, customer count, etc.).
4. Write one clear CTA. Never more than one.
5. Match the category voice: {category_voice}
6. Keep the message under 200 characters for WhatsApp-style delivery.

OUTPUT FORMAT (strict JSON, no markdown):
{
  "body": "...",
  "cta": "yes_no|open_ended|confirm|choose|none",
  "suppression_key": "{trigger_type}:{category}:{YYYY-WNN}",
  "send_as": "vera",
  "rationale": "Signal chosen: ... Reason: ..."
}

CONTEXT:
{full_context_json}
```

---

## Tick Strategy

On each `/v1/tick` call:
1. Get all loaded merchants from storage
2. Get available triggers from the request
3. For each (merchant, trigger) pair — check suppression (skip if already sent this week)
4. Score pairs: prefer triggers that match the merchant's category AND have a performance signal (dip, spike, recall)
5. Take top N pairs (where N ≤ 20)
6. For each pair, call `compose()` to generate the action
7. Add suppression key to suppression set
8. Return actions list

**Important**: Do NOT call Codex API in a loop one-by-one. Batch your compose calls or run them concurrently with threading to stay within the 30s timeout.

---

## Reply Strategy

On each `/v1/reply` call:
1. Load conversation history from storage (keyed by conversation_id)
2. Append the new message
3. Determine intent: accept / reject / question / off-topic
4. If accept → compose next step (usually a draft or confirmation)
5. If reject → graceful close, offer alternative
6. If question → answer from merchant context
7. If off-topic → redirect, set action="wait" or "end"

---

## Deployment: Railway (Fastest Option)

1. Go to railway.app → New Project → Deploy from GitHub
2. Connect your GitHub repo
3. Set environment variable: `GROQ_API_KEY=gsk_...` (get free key at console.groq.com)
4. Railway auto-detects Python and uses Procfile
5. Get your public URL (e.g., `https://magicpin-ai-production.up.railway.app`)

**Procfile:**
```
web: gunicorn app:app --workers 2 --timeout 60 --bind 0.0.0.0:$PORT
```

**railway.toml:**
```toml
[build]
builder = "NIXPACKS"

[deploy]
startCommand = "gunicorn app:app --workers 2 --timeout 60 --bind 0.0.0.0:$PORT"
healthcheckPath = "/v1/healthz"
healthcheckTimeout = 10
restartPolicyType = "ON_FAILURE"
```

---

## Testing Locally

```bash
# Install deps
pip install flask groq gunicorn requests

# Run locally
python app.py

# Test healthz
curl http://localhost:5000/v1/healthz

# Push a merchant context
curl -X POST http://localhost:5000/v1/context \
  -H "Content-Type: application/json" \
  -d '{"scope":"merchant","context_id":"m_001","version":1,"payload":{"identity":{"name":"Dr. Meera","category":"dentist"},"performance":{"ctr":2.1,"peer_median_ctr":3.0},"offers":[{"name":"Dental Cleaning","price":299}]},"delivered_at":"2026-05-02T10:00:00Z"}'

# Test a tick
curl -X POST http://localhost:5000/v1/tick \
  -H "Content-Type: application/json" \
  -d '{"now":"2026-05-02T10:30:00Z","available_triggers":["trg_001"]}'
```

---

## What Will Get You a High Score

1. **Never hallucinate** — every number in the body must come from the payload.
2. **One signal, one CTA** — the judge penalizes multi-CTA messages.
3. **Rationale is scored** — write a real rationale, not boilerplate.
4. **Suppression works** — duplicate messages tank your score.
5. **Reply flow is stateful** — remember conversation history, don't restart.
6. **Fast responses** — stay well under 30s timeout.

---

## Implementation Order

1. `storage.py` — get context storage working
2. `app.py` — stub all 5 endpoints returning correct shapes
3. `GET /v1/healthz` and `GET /v1/metadata` — trivial, do these first
4. `POST /v1/context` — implement idempotent storage
5. `prompts.py` — write CATEGORY_VOICE and system prompt template
6. `composer.py` — implement compose() with Anthropic API call
7. `POST /v1/tick` — wire composer to tick, implement suppression
8. `POST /v1/reply` — implement conversation state + reply logic
9. Deploy to Railway
10. Test with curl against live URL
11. Download challenge zip locally, run `judge_simulator.py` against your live URL
12. Iterate on prompt quality based on simulator output
13. Submit URL
