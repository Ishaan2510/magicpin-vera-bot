# EXECUTION PLAN — Magicpin AI Challenge
# Deadline: 02 May 2026, 11:59 PM IST

---

## YOUR CURRENT STATE

You are in: ~/Ishaan/claude-code-projects/magicpin_ai/

You need to:
1. Copy the provided files into this folder
2. Launch Claude Code
3. Follow the first prompt
4. Deploy to Railway
5. Submit the URL

---

## STEP 1: Copy files into your project folder

Copy these files from wherever you downloaded them into:
~/Ishaan/claude-code-projects/magicpin_ai/

Files to copy:
- CLAUDE.md
- app.py
- storage.py
- composer.py
- prompts.py
- requirements.txt
- Procfile
- railway.toml
- README.md
- FIRST_PROMPT.md (this is just for you, don't submit)

---

## STEP 2: Get your Anthropic API Key

Go to: https://console.groq.com/keys
Create a new key. Copy it. You will need it for:
- The GROQ_API_KEY environment variable locally
- The Railway environment variable

---

## STEP 3: Initialize git

In your terminal, inside ~/Ishaan/claude-code-projects/magicpin_ai/:

```bash
git init
git add .
git commit -m "Vera message engine - initial implementation"
```

---

## STEP 4: Push to GitHub

1. Go to github.com → New repository → name it "magicpin-vera-bot"
2. Make it PUBLIC (Railway free tier works better with public repos)
3. Follow GitHub's push instructions:

```bash
git remote add origin https://github.com/YOUR_USERNAME/magicpin-vera-bot.git
git branch -M main
git push -u origin main
```

---

## STEP 5: Launch Claude Code

```bash
cd ~/Ishaan/claude-code-projects/magicpin_ai
claude
```

When Claude Code starts, paste the ENTIRE contents of FIRST_PROMPT.md as your first message.
Then let Claude Code run. It will:
- Download and read the challenge zip
- Verify all endpoints
- Run the judge simulator
- Improve the prompt based on results

Provide your GROQ_API_KEY when Claude Code asks for it.

---

## STEP 6: Deploy to Railway

1. Go to https://railway.app
2. Sign up / log in with GitHub
3. Click "New Project" → "Deploy from GitHub repo"
4. Select "magicpin-vera-bot"
5. Railway auto-detects Python — let it build
6. Once deployed, click "Variables" → Add:
   - Name: GROQ_API_KEY
   - Value: sk-ant-...your key...
7. Railway restarts automatically
8. Click "Domains" → Copy your public URL (looks like https://magicpin-vera-bot-production.up.railway.app)

---

## STEP 7: Verify the live deployment

```bash
curl https://YOUR-RAILWAY-URL.up.railway.app/v1/healthz
# Should return: {"status":"ok","uptime_seconds":...,"contexts_loaded":{...}}

curl https://YOUR-RAILWAY-URL.up.railway.app/v1/metadata
# Should return your team info
```

---

## STEP 8: Run judge simulator against live URL

In judge_simulator.py (from the challenge zip), set:
- BOT_URL = "https://YOUR-RAILWAY-URL.up.railway.app"
- LLM_PROVIDER = "groq" (or whatever it expects)
- LLM_API_KEY = your Anthropic key

Then run:
```bash
python judge_simulator.py
```

Check scores on all 5 dimensions. If any is below 6, ask Claude Code:
"The simulator scored [dimension] at [score]. Here is the full output: [paste]. Fix the composer prompt to improve this."

---

## STEP 9: Submit

Go to: https://partners.magicpin.in/vera/ai-challenge
Click "Apply now" → Fill the form:
- Full name: Ishaan Goswami
- Email: your email
- Phone: your number
- Submission URL: https://YOUR-RAILWAY-URL.up.railway.app
- LinkedIn: your LinkedIn

Submit before 11:59 PM IST tonight.

---

## STEP 10: Keep the bot live

DO NOT turn off Railway for the next 3 days.
The judge harness runs scoring on fresh scenarios after submission closes.
Railway free tier gives you 500 hours/month — more than enough.

---

## IF SOMETHING BREAKS

**Bot returns 500:** Check Railway logs (click "Deployments" → latest deploy → "View Logs")
**Groq API error:** Verify GROQ_API_KEY is set in Railway Variables and has credits
**Timeout on tick:** The compose() calls are concurrent — if still timing out, reduce max_workers to 4
**JSON parse error:** Claude Code will fix this by tightening the output format in the prompt

---

## WHAT WINS THIS CHALLENGE

The judge FAQ says: "Bots that pattern-match the simulator will fail. Bots that ground every
output in the context they've actually been given will not."

Your competitive advantage: You have category-specific voice guidelines, single-signal discipline,
specificity enforcement at the prompt level, and stateful conversation memory. Most submissions
will be generic templates. Yours will not be.

Good luck. You can do this.
