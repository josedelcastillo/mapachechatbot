"""
Prompt construction and knowledge base loading from S3.
KB is cached in Lambda memory after first load.
"""

import json
import logging
import os

import boto3

from memory import get_recent_messages

logger = logging.getLogger()

S3_BUCKET = os.environ["S3_BUCKET_NAME"]
KB_KEY = os.environ["KB_S3_KEY"]
BADGE_PROGRESS_KEY = os.environ.get("BADGE_PROGRESS_S3_KEY", "badge_progress/all_mapaches.json")

_kb_cache: dict | None = None
_badge_progress_cache: dict | None = None
_s3_client = None


def _s3():
    global _s3_client
    if _s3_client is None:
        region = os.environ.get("AWS_REGION_NAME", "us-east-1")
        _s3_client = boto3.client("s3", region_name=region)
    return _s3_client


def load_knowledge_base() -> dict:
    """Load KB JSON from S3, caching in Lambda memory."""
    global _kb_cache
    if _kb_cache is not None:
        return _kb_cache

    try:
        obj = _s3().get_object(Bucket=S3_BUCKET, Key=KB_KEY)
        _kb_cache = json.loads(obj["Body"].read().decode("utf-8"))
        logger.info("Knowledge base loaded from S3")
    except Exception:
        logger.exception("Failed to load knowledge base from S3")
        _kb_cache = {}

    return _kb_cache


def load_badge_progress() -> dict:
    """Load badge progress JSON from S3, caching in Lambda memory.
    Returns dict: { learner_name: { approved: [...], in_progress: {...} } }
    Empty dict on any failure — chatbot degrades gracefully.
    """
    global _badge_progress_cache
    if _badge_progress_cache is not None:
        return _badge_progress_cache

    try:
        obj = _s3().get_object(Bucket=S3_BUCKET, Key=BADGE_PROGRESS_KEY)
        data = json.loads(obj["Body"].read().decode("utf-8"))
        _badge_progress_cache = data.get("learners", {})
        logger.info("Badge progress loaded — %d learners", len(_badge_progress_cache))
    except Exception:
        logger.warning("Badge progress not available from S3 (scraper may not have run yet)")
        _badge_progress_cache = {}

    return _badge_progress_cache


def _build_badge_index(progress: dict) -> dict[str, list[str]]:
    """Inverted index: badge_name → [list of learner names who approved it].
    Max 3 names per badge to keep prompt compact.
    """
    index: dict[str, list[str]] = {}
    for name, data in progress.items():
        for badge in data.get("approved", []):
            index.setdefault(badge, [])
            if len(index[badge]) < 3:
                index[badge].append(name)
    return index


def detect_role_hint(user_message: str) -> str | None:
    """
    Lightweight keyword scan to hint at the Mapache's Hero's Journey role.
    Returns role id string or None.
    """
    kb = load_knowledge_base()
    heuristics: dict = kb.get("heuristics", {})
    lower_msg = user_message.lower()

    for nivel_id, keywords in heuristics.items():
        if any(kw.lower() in lower_msg for kw in keywords):
            return nivel_id

    return None


_SPANISH_CHARS = set("áéíóúüñ¿¡àèìòùâêîôûãõäëïöÿ")
_SPANISH_WORDS = {"que", "de", "no", "en", "es", "mi", "se", "su", "lo", "le",
                  "con", "por", "para", "una", "como", "pero", "más", "muy",
                  "ya", "también", "cuando", "esto", "ese", "eso", "sin"}


def detect_language(user_message: str) -> str | None:
    """
    Heuristic language detection: returns 'es' for Spanish, 'en' for English, None if uncertain.
    Checks for Spanish-specific characters and common Spanish function words.
    """
    lower = user_message.lower()

    # Spanish accented characters or inverted punctuation are a strong signal
    if any(ch in _SPANISH_CHARS for ch in lower):
        return "es"

    # Count common Spanish function words
    words = set(lower.split())
    spanish_hits = len(words & _SPANISH_WORDS)
    if spanish_hits >= 2:
        return "es"

    # Default to English if message has content but no Spanish signals
    if len(user_message.strip()) > 3:
        return "en"

    return None


def _build_relevant_kb(
    kb: dict,
    role_hint: str | None,
    confirmed_role: str | None,
    badge_index: dict[str, list[str]] | None = None,
) -> str:
    """Return a compact KB string prioritizing the detected role.
    badge_index: inverted index of badge_name → [mapache names who completed it].
    """
    niveles = kb.get("niveles", [])
    active_id = confirmed_role or role_hint

    if active_id:
        primary = [n for n in niveles if n.get("id") == active_id]
        secondary = [n for n in niveles if n.get("id") != active_id]
        ordered = primary + secondary
    else:
        ordered = niveles

    idx = badge_index or {}
    compact = []
    for nivel in ordered:
        nivel_name = nivel.get("name", nivel.get("id", ""))
        for rol in nivel.get("roles", []):
            rol_name = rol.get("name", "")
            badges = []
            for b in rol.get("badges", []):
                entry = {
                    "name": b["name"],
                    "description": b.get("description", ""),
                    "_nivel": nivel_name,
                    "_rol": rol_name,
                }
                peers = idx.get(b["name"], [])
                if peers:
                    entry["_mapaches_who_completed"] = peers
                badges.append(entry)

            books = []
            for b in rol.get("books", []):
                entry = {
                    "title": b["title"],
                    "author": b.get("author", ""),
                    "description": b.get("description", ""),
                    "_nivel": nivel_name,
                    "_rol": rol_name,
                }
                peers = idx.get(b.get("title", ""), [])
                if peers:
                    entry["_mapaches_who_completed"] = peers
                books.append(entry)

            if badges or books:
                compact.append({
                    "nivel": nivel_name,
                    "rol": rol_name,
                    "monster": rol.get("monster"),
                    "badges": badges,
                    "books": books,
                })

    return json.dumps(compact, ensure_ascii=False, indent=2)


def build_prompt(session: dict, user_message: str, role_hint: str | None) -> str:
    kb = load_knowledge_base()
    progress = load_badge_progress()
    badge_index = _build_badge_index(progress)

    recent = get_recent_messages(session)
    recent_text = _format_messages(recent)

    summary = session.get("summary", "")
    language = session.get("detected_language", "")
    confirmed_role = session.get("detected_role")

    language_instruction = (
        "Respond in SPANISH (español). The user writes in Spanish."
        if language == "es"
        else "Respond in ENGLISH. The user writes in English."
        if language == "en"
        else "Always respond in the SAME LANGUAGE the user writes in."
    )

    role_context = ""
    active_role = confirmed_role or role_hint
    if active_role:
        role_context = (
            f"\n[DETECTED STAGE: '{active_role.upper()}' — "
            f"prioritize badges and books from this role's levels.]\n"
        )

    kb_text = _build_relevant_kb(kb, role_hint, confirmed_role, badge_index)

    prompt = f"""You are a warm, empathetic guide for Mapaches (parents) at Tinkuy Marka Academy.

RULES — follow strictly:
1. LANGUAGE: {language_instruction} Never mix languages.
2. Badges and books MUST come ONLY from the KNOWLEDGE BASE below. Copy names and titles verbatim. Never invent them.
3. Do NOT mention role names (Guardian, Mentor, etc.) or level names (Hearthkeeper, etc.) in your response.
4. Be specific: tie recommendations directly to what the Mapache described.
5. Build on previous messages — do not repeat advice already given.
6. SUBJECT AWARENESS: The Mapache (parent) is the protagonist of their own journey. Badges and books serve the Mapache directly — for their personal growth, professional life, emotions, and relationships. Some badges involve their Puma (child), others are purely for the Mapache themselves. When the Mapache asks about their OWN situation (fears, independence, decisions, emotions), frame recommendations around THEIR experience — not their child's. Only reference the Puma when the Mapache explicitly mentions their child.
7. PEER MAPACHES: Some badges/books in the knowledge base include a "_mapaches_who_completed" list — these are real Tinkuy Marka parents who have already completed that badge. When recommending such a badge or book, add a brief note (1 sentence) mentioning up to 3 of those names as fellow Mapaches they could ask for their experience. Only mention peers when the field is present. Never invent names.
{role_context}
---

PREVIOUS CONTEXT:
{summary or "First message."}

RECENT MESSAGES:
{recent_text or ""}

KNOWLEDGE BASE (use ONLY these badges and books — copy names/titles verbatim):
Each item includes "_rol", "_nivel", and "description" — use description to explain why it applies to the Mapache's situation.
{kb_text}

---

USER MESSAGE:
{user_message}

---

RESPONSE TYPE — choose based on the message:

TYPE A — Conversational response (use when: the Mapache greets you, thanks you, makes small talk, is venting or sharing feelings, or you need more context to make a useful recommendation):
- Respond naturally and warmly to what they said
- 2–4 sentences of empathetic reflection if they shared something personal
- Ask 1 follow-up question to understand their situation better
- NO badges or books
- Use emojis naturally to warm up the tone

TYPE B — Guidance with recommendations (use when you have enough context to understand their situation and a recommendation would genuinely help):
- 2–3 sentences of empathetic reflection naming the challenge or fear (no role labels)
- Start with a fitting emoji
- 🏅 Badges recomendados:
  - **<nombre exacto>** *(Rol: <_rol> — Nivel: <_nivel>)*: <one sentence explaining why it applies, based on the badge description and the Mapache's situation>. [If _mapaches_who_completed is present: "💬 Ya lo lograron: <names> — puedes consultarles su experiencia."]
  (2 to 4 badges most relevant to what the Mapache described)
- 📚 Libros recomendados:
  - **<título> — <autor>** *(Rol: <_rol> — Nivel: <_nivel>)*: <one sentence explaining why it applies, based on the book description and the Mapache's situation>. [If _mapaches_who_completed is present: "💬 Ya lo leyeron: <names>."]
  (1 to 3 books most relevant to what the Mapache described)

TYPE C — Badge or book detail (use when the Mapache explicitly asks about a specific badge or book, e.g. "¿de qué trata ese badge?", "cuéntame más del libro", "what is that badge about?", "más info sobre X"):
- Start with the badge/book name in bold on its own line
- Then the full description from the knowledge base verbatim (do NOT paraphrase or add an intro sentence before it — go straight to the description)
- Then 1–2 sentences connecting it to the Mapache's specific situation (only if it adds something new beyond the description)
- DO NOT recommend any other badges or books — answer ONLY about the one they asked
- DO NOT use a "Badges recomendados" or "Libros recomendados" section header

TYPE D — More recommendations (use when the Mapache asks for additional badges or books beyond what was already suggested, e.g. "¿hay otros badges?", "dame más libros", "any other recommendations?"):
- Do NOT repeat badges or books already mentioned in the conversation
- Suggest 2–3 new badges and/or 1–2 new books not yet mentioned
- Same format as TYPE B recommendations

Default to TYPE A for the first 1–2 exchanges. Move to TYPE B once you understand their situation well enough to make meaningful recommendations. Always use TYPE C when explicitly asked about a specific badge or book. Use TYPE D when asked for more options beyond what was already given.
"""

    return prompt


def _format_messages(messages: list) -> str:
    lines = []
    for msg in messages:
        role = "Parent" if msg["role"] == "user" else "Guide"
        lines.append(f"{role}: {msg['content']}")
    return "\n".join(lines)
