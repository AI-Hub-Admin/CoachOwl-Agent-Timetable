from __future__ import annotations

import re
from typing import Dict


def _topic(prompt: str) -> str:
    p = (prompt or "").lower()
    if any(k in p for k in ["promotion", "raise", "salary", "title", "level up", "boss"]):
        return "promotion"
    if any(k in p for k in ["network", "networking", "conference", "event", "meetup"]):
        return "networking"
    if any(k in p for k in ["conflict", "argument", "difficult", "coworker", "manager", "toxic", "politics"]):
        return "conflict"
    return "general"


def career_advice_markdown(prompt: str) -> str:
    topic = _topic(prompt)
    if topic == "promotion":
        return """### Promotion game plan (CoachOwl)
1) **Clarify the ask (today):** confirm the exact title/level, scope, and compensation band you’re targeting.
2) **Build the evidence pack (next 3–7 days):** 3–5 wins, quantified impact, and “next-level” responsibilities you already cover.
3) **Propose a timeline (this week):** “Can we align on criteria + a review date in 4 weeks?”
4) **Negotiate smart:** anchor to market + internal parity, ask for total comp, and have 2–3 alternatives (bonus, title now + comp later, scope expansion).
5) **Follow-up cadence:** recap in writing within 24h; weekly 10-min check-in until decision.

If you paste what you’ve already said to your boss and their response, I’ll draft a clean follow-up message."""
    if topic == "networking":
        return """### Networking event plan (CoachOwl)
**Before (24–48h):**
- Pick 3 targets (people/companies), 1 learning goal, 1 help-offer.
- Prepare a 15-second intro + 2 questions (“What’s your team optimizing for this quarter?”).

**During:**
- Aim for 4 quality conversations, not 40.
- Use the loop: *context → curiosity → value → next step*.

**After (within 24h):**
- Send 3 follow-ups: 1 specific takeaway + 1 lightweight next step (15-min coffee / intro / resource).

Tell me your role + the event type and I’ll generate a personalized script."""
    if topic == "conflict":
        return """### Handling workplace conflict (CoachOwl)
1) **Name the problem neutrally:** “When X happened, the impact was Y.”
2) **Ask for a change:** “Next time, can we do Z?”
3) **Document facts:** dates, outcomes, and any agreed actions.
4) **Escalate with options:** manager/HR with a proposed plan, not just complaints.
5) **Protect your energy:** set boundaries; avoid high-emotion channels when possible.

If you describe the situation in 3–5 bullets, I’ll draft a calm message + a meeting agenda."""
    return """### Life & Career coaching (CoachOwl)
Tell me:
1) your goal (what “good” looks like),
2) constraints (time, politics, risk),
3) what you’ve tried.

I’ll respond with a step-by-step plan + a short script you can actually use."""

