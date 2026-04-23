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

_kb_cache: dict | None = None
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


def _build_relevant_kb(kb: dict, role_hint: str | None, confirmed_role: str | None) -> str:
    """
    Return a compact KB string: all levels from the detected role first,
    then remaining roles. This keeps the prompt focused without hiding options.
    """
    niveles = kb.get("niveles", [])
    active_id = confirmed_role or role_hint

    if active_id:
        primary = [n for n in niveles if n.get("id") == active_id]
        secondary = [n for n in niveles if n.get("id") != active_id]
        ordered = primary + secondary
    else:
        ordered = niveles

    # Emit only the fields needed for recommendations, tagging each item with nivel+rol for citation
    compact = []
    for nivel in ordered:
        nivel_name = nivel.get("name", nivel.get("id", ""))
        for rol in nivel.get("roles", []):
            rol_name = rol.get("name", "")
            badges = [
                {**b, "_nivel": nivel_name, "_rol": rol_name}
                for b in rol.get("badges", [])
            ]
            books = [
                {**b, "_nivel": nivel_name, "_rol": rol_name}
                for b in rol.get("books", [])
            ]
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

    kb_text = _build_relevant_kb(kb, role_hint, confirmed_role)

    prompt = f"""You are a warm, empathetic guide for Mapaches (parents) at Tinkuy Marka Academy.

RULES — follow strictly:
1. LANGUAGE: {language_instruction} Never mix languages.
2. Badges and books MUST come ONLY from the KNOWLEDGE BASE below. Copy names and titles verbatim. Never invent them.
3. Do NOT mention role names (Guardian, Mentor, etc.) or level names (Hearthkeeper, etc.) in your response.
4. Keep the empathetic response brief — 2 to 3 sentences max.
5. Be specific: tie recommendations directly to what the Mapache described.
6. Build on previous messages — do not repeat advice already given.
{role_context}
---

PREVIOUS CONTEXT:
{summary or "First message."}

RECENT MESSAGES:
{recent_text or ""}

KNOWLEDGE BASE (use ONLY these badges and books — copy names/titles verbatim):
Each item includes "_rol" (specific name, e.g. Hearthkeeper) and "_nivel" (category, e.g. Guardian) — include both in your response exactly as shown.
{kb_text}

---

USER MESSAGE:
{user_message}

---

Respond using EXACTLY this structure (in the user's language). Use emojis naturally to warm up the tone:

[2–3 sentences: empathetic reflection on what the Mapache shared, naming the challenge or fear without labeling their role. Start with a fitting emoji.]

🏅 Badges recomendados:
- **<nombre exacto>** *(Rol: <_rol> — Nivel: <_nivel>)*: <una oración explicando por qué aplica a su situación, basándote en la descripción del badge>
(include 2 to 4 badges most relevant to what the Mapache described)

📚 Libros recomendados:
- **<título> — <autor>** *(Rol: <_rol> — Nivel: <_nivel>)*: <una oración explicando por qué aplica a su situación, basándote en la descripción del libro>
(include 1 to 3 books most relevant to what the Mapache described)
"""

    return prompt


def _format_messages(messages: list) -> str:
    lines = []
    for msg in messages:
        role = "Parent" if msg["role"] == "user" else "Guide"
        lines.append(f"{role}: {msg['content']}")
    return "\n".join(lines)
