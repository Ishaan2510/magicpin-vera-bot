# Vera Message Engine — Magicpin AI Challenge Submission

**Team:** Ishaan Goswami  
**Model:** llama-3.3-70b-versatile  
**Approach:** Context-grounded single-signal composer with category-aware prompting and suppression

---

## Architecture

A Flask bot exposing the 5 required endpoints. Core logic is a `compose()` function
that calls Claude API with a carefully structured prompt — one that forces the model
to pick the single most compelling signal from the provided context and build a specific,
grounded message around it.

```
app.py        → Flask endpoints, tick orchestration with thread pool
storage.py    → Thread-safe in-memory store for all 4 context scopes + suppression
composer.py   → compose_action() and compose_reply() using Anthropic API
prompts.py    → Category voice guidelines, system prompt templates
```

## Design Decisions

**Single-signal discipline:** The system prompt explicitly forces the model to choose ONE signal
(trigger + merchant metric + category insight) rather than mention all available facts.
This matches what the judge scores highest on decision quality.

**Specificity enforcement:** The prompt rules require at least one specific number or named offer
from the context in every body. Hallucinated facts cause rule-triggered score zeroes, so grounding
is enforced at the prompt level.

**Category voice:** Five distinct voice guidelines (dentist = clinical, salon = visual, 
restaurant = timely, gym = motivational, pharmacy = utility-first) ensure tone is never generic.

**Suppression:** Keys follow `{trigger_type}:{category}:{YYYY-WNN}` format. Suppression is
checked before composing and set atomically after a successful action is generated. Prevents
duplicate messages within the same week window.

**Concurrent tick:** Candidate pairs are scored by priority and composed concurrently using
a thread pool (max 8 workers, 25s timeout) to stay within the 30s tick constraint.

**Stateful reply:** Conversation history is stored per `conversation_id`. Each reply call
appends to history, and the full history is passed to the model for contextual continuation.

## Running Locally

```bash
pip install -r requirements.txt
export GROQ_API_KEY=gsk_...
python app.py

# Test endpoints
curl http://localhost:5000/v1/healthz
curl http://localhost:5000/v1/metadata
```
