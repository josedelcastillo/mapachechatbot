You are an expert AWS serverless architect and AI engineer.

Build a production-ready, low-cost conversational RAG chatbot for an educational platform called Tinkuy Marka (Acton Academy network).

---

# 🎯 GOAL

The chatbot guides **Mapaches** (parents) through their Hero's Journey at Tinkuy Marka Academy.

It helps Mapaches:
- Understand which stage of the Hero's Journey they are in
- Identify the monsters (fears/challenges) they are facing
- Receive empathetic guidance aligned with their current role
- Feel seen and supported in their parenting journey

> **Terminology:**
> - **Mapaches** = parents
> - **Pumas** = children/students

The system must maintain conversation memory across a session.

**Language:** Always respond in the same language the user writes in. If the user writes in Spanish, respond in Spanish. If in English, respond in English. Never switch languages unless the user does.

---

# 🗺️ KNOWLEDGE BASE — Mapache Badge Descriptions

The knowledge base is derived from **"Family Badges: Descriptions by Role and Level"** (Mapache Badge Descriptions PDF).

It is organized following the Hero's Journey framework for Mapaches, with two types of recommendations per level:
- **Badges:** Practical challenges/activities the Mapache can complete
- **Badge Books:** Recommended books to read

The Hero's Journey emotional framework (roles, monsters, traits) from "The Parent's Hero's Journey at Tinkuy Marka Academy" is embedded in the same JSON to help identify the Mapache's current stage.

## The 5 Roles

### 1. Guardian (Protector & Provider) — The Beginning
- **Key Traits:** Protective, nurturing, reliable
- **Task:** Creating a secure foundation for their Puma's growth
- **Challenge:** Trusting an unconventional approach
- **Monsters:** Doubt, Judgment, Control
- **Levels:**
  - Hearthkeeper: Builds a nurturing home. Faces Doubt.
  - Pathkeeper: Trusts the journey of deep education. Battles Judgment.
  - Mindkeeper: Embraces learner-driven principles. Relinquishes Control.

### 2. Mentor (Guide & Role Model) — Igniting the Spark
- **Key Traits:** Patient, wise, disciplined
- **Task:** Supporting and modeling lifelong learning
- **Challenge:** Balancing guidance with independence
- **Monsters:** Indifference, Resistance, Uncertainty
- **Levels:**
  - Torchbearer: Ignites curiosity and love for learning. Faces Indifference.
  - Keystone: Holds firm boundaries while fostering agency. Battles Resistance.
  - Northstar: Guides with clarity and purpose. Overcomes Uncertainty.

### 3. Challenger (Catalyst for Growth) — Facing the Tests
- **Key Traits:** Encouraging, firm, strategic
- **Task:** Encouraging independence and problem-solving
- **Challenge:** Overcoming the instinct to rescue
- **Monsters:** Self-Doubt, Overprotection, Defeat
- **Levels:**
  - Gatekeeper: Tests readiness for greater responsibility. Faces Self-Doubt.
  - Forge Master: Shapes resilience through struggle. Battles Overprotection.
  - Trialwarden: Fosters growth through necessary challenges. Overcomes Defeat.

### 4. Ally (Supportive but Unintrusive) — Trusting the Hero
- **Key Traits:** Respectful, empowering, steadfast
- **Task:** Providing trust, perspective, and encouragement
- **Challenge:** Accepting that their Puma's path may look different
- **Monsters:** Wraith (fear of irrelevance), Disapproval, Regret
- **Levels:**
  - Silent Watcher: Observes without interference. Faces Wraith.
  - Steady Flame: A warm, guiding presence without control. Battles Disapproval.
  - Guiding Hand: Offers support without taking over. Overcomes Regret.

### 5. Legacy (Inspiration & Imprint) — A New Beginning
- **Key Traits:** Inspirational, content, timeless
- **Task:** Celebrating the journey and embracing a new phase
- **Challenge:** Letting go with pride and confidence
- **Monsters:** Drift, Distance, Change
- **Levels:**
  - Echo of Purpose: Their values live on through their Puma's actions. Faces Drift.
  - Beacon of Trust: A steady light as their Puma forges ahead. Battles Distance.
  - Legacy Keeper: Inspires the next generation by example. Overcomes Change.

---

# 🏗️ ARCHITECTURE (STRICT)

Use ONLY:

- AWS Lambda (Python)
- API Gateway
- S3 (store knowledge base as structured JSON)
- DynamoDB (store sessions + memory)
- Amazon Bedrock:
  - Claude Haiku (chat)

DO NOT use:
- Vector databases
- External services

---

# 📚 KNOWLEDGE BASE — S3 JSON Structure

Store the knowledge base in S3 as a single JSON. Load at runtime (cache in Lambda memory).

```json
{
  "roles": [
    {
      "name": "Guardian",
      "subtitle": "Protector & Provider",
      "stage": "The Beginning",
      "traits": ["Protective", "nurturing", "reliable"],
      "task": "Creating a secure foundation for their Puma's growth",
      "challenge": "Trusting an unconventional approach",
      "monsters": ["Doubt", "Judgment", "Control"],
      "levels": [
        {
          "name": "Hearthkeeper",
          "description": "Builds a nurturing home",
          "monster": "Doubt"
        },
        {
          "name": "Pathkeeper",
          "description": "Trusts the journey of deep education",
          "monster": "Judgment"
        },
        {
          "name": "Mindkeeper",
          "description": "Embraces learner-driven principles",
          "monster": "Control"
        }
      ]
    }
    // ... remaining 4 roles follow the same structure
  ]
}
```

---

# 🧠 MEMORY DESIGN (CRITICAL)

Implement hybrid memory:

## 1. Recent messages (short-term)
- Keep last 6 messages

## 2. Conversation summary (long-term)
- Stored in DynamoDB
- Updated every 10 messages

DynamoDB item:

```json
{
  "session_id": "string",
  "messages": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "summary": "short summary of conversation",
  "detected_role": "Guardian|Mentor|Challenger|Ally|Legacy|null",
  "detected_language": "es|en|...",
  "updated_at": "timestamp",
  "ttl": "timestamp"
}
```

TTL = 7 days

---

# 🔁 FLOW

1. Receive request:
```json
{
  "session_id": "...",
  "message": "..."
}
```

2. Load session from DynamoDB
3. Extract:
   - last 6 messages
   - summary
   - detected_role (if already identified)
   - detected_language

4. Build prompt with:
   - summary
   - recent messages
   - knowledge base (full roles JSON)
   - language instruction

5. Call Bedrock Claude

6. Store:
   - user message
   - assistant response
   - update detected_language if changed

7. Every 10 messages:
   - generate summary using Claude
   - store updated summary + detected_role

---

# ✨ PROMPT DESIGN

## Chat prompt:

```
You are a compassionate guide for Mapaches (parents) at Tinkuy Marka Academy.
You support them through their Hero's Journey — from Guardian to Legacy.

IMPORTANT: Always respond in the SAME LANGUAGE the user writes in.
If the user writes in Spanish, respond fully in Spanish.
If in English, respond in English. Never mix languages.

Your role:
- Be warm, empathetic, and non-judgmental
- Listen deeply to understand what the Mapache is experiencing
- Identify which role they are currently in (Guardian, Mentor, Challenger, Ally, Legacy)
- Identify which monster (fear/challenge) they may be facing
- Offer guidance rooted in their current stage of the journey
- Help them see their struggle as a meaningful part of the hero's journey

You MUST:
- Use ONLY the provided knowledge base
- Use conversation context
- Avoid generic advice
- Reference specific levels, monsters, or traits from the Hero's Journey when relevant
- Build on previous messages

---

SUMMARY:
{summary}

RECENT MESSAGES:
{recent_messages}

KNOWLEDGE BASE:
{kb_json}

USER:
{user_input}

---

OUTPUT FORMAT:

[Empathetic reflection on what the Mapache shared]

[Identification of their current stage/role and level, if apparent]

[Guidance rooted in the Hero's Journey framework]

[Optional: name the monster they may be facing and reframe it as part of growth]

Recommended Badges (1–2 from their current level):
- Name:
  Why:

Recommended Books (1 from their current level):
- Title / Author:
  Why:
```

---

# 🧾 SUMMARY PROMPT

```
Summarize the conversation focusing on:
- Mapache's main concerns and emotional state
- Puma's behaviors or situations mentioned
- Current Hero's Journey role identified (if any)
- Monsters (fears/challenges) identified
- Guidance given so far
- Language used by the user

Max 120 words.
```

---

# 🧩 FILE STRUCTURE

```
handler.py        # Lambda entry point
memory.py         # DynamoDB session logic
rag.py            # Prompt building + KB loading from S3
bedrock.py        # Claude Haiku API call
summarizer.py     # Conversation summary generation
knowledge_base/
  journey.json    # Full Hero's Journey knowledge base
```

---

# ⚡ REQUIREMENTS

- Python 3.11
- Use boto3
- Clean, modular code
- Include error handling
- Include environment variables:
  - `S3_BUCKET_NAME`
  - `KB_S3_KEY` (e.g., `knowledge_base/journey.json`)
  - `DYNAMODB_TABLE_NAME`
  - `BEDROCK_MODEL_ID` (default: `anthropic.claude-haiku-20240307-v1:0`)
  - `AWS_REGION`
- Optimize for low cost (minimal tokens)

---

# 🚀 HEURISTIC LAYER

Apply keyword boosting to detect the Mapache's stage and monsters before calling Claude:

| Keywords detected | Likely stage/monster |
|-------------------|----------------------|
| "miedo", "fear", "duda", "doubt", "seguro", "sure" | Guardian / Doubt |
| "juzgan", "judging", "críticas", "criticism" | Guardian / Judgment |
| "controlar", "control", "micromanage" | Guardian / Control |
| "no le importa", "doesn't care", "indiferente" | Mentor / Indifference |
| "resistencia", "resistance", "se niega", "refuses" | Mentor / Resistance |
| "no sé qué sigue", "don't know what's next" | Mentor / Uncertainty |
| "sobreproteger", "overprotect", "rescatar", "rescue" | Challenger / Overprotection |
| "demasiado duro", "too harsh", "cruel" | Challenger / Self-Doubt |
| "irrelevante", "irrelevant", "me necesita menos" | Ally / Wraith |
| "desapruebo", "disapprove", "no me gusta su decisión" | Ally / Disapproval |
| "me arrepiento", "regret", "culpa", "guilt" | Ally / Regret |
| "se va", "leaving", "se aleja", "drifting away" | Legacy / Drift or Distance |

Pass detected stage hint to the prompt as additional context.

---

# 📦 OUTPUT

Return:
1. Full working code for all 5 files
2. `knowledge_base/journey.json` — complete structured knowledge base
3. Deployment steps (Lambda + API Gateway + S3 + DynamoDB)
4. Example request/response in both Spanish and English
5. Notes on cost optimization

---

Build the complete solution.
