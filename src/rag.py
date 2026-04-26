"""
Prompt construction and knowledge base loading.
KB and badge progress are both loaded from files bundled with the Lambda package,
cached in module globals for the container lifetime.
"""

import csv
import json
import logging
import os
import re
from datetime import datetime

import boto3

from memory import get_recent_messages

logger = logging.getLogger()

S3_BUCKET = os.environ.get("S3_BUCKET_NAME", "")

_LEVEL_PREFIX_RE = re.compile(r"^L\d+\s*-\s*")

# Casa membership: mapache name (normalized) → casa
_CASAS: dict[str, str] = {
    "yuri ccasa": "Chavin", "martha aguero": "Chavin",
    "arturo arellano": "Chavin", "gracia dextre": "Chavin",
    "gustavo zambrano": "Chavin", "sandra lizardo": "Chavin",
    "luz chang navarro": "Chavin", "rodolfo mondion": "Chavin",
    "jose castañeda": "Chavin", "julyeth alcantara": "Chavin", "fernando ureta": "Chavin",
    "brissy cáceres": "Chavin", "brissy caceres": "Chavin",
    "jose del castillo": "Wari", "lucía guerrero": "Wari", "lucia guerrero": "Wari",
    "marco ramos": "Wari", "kailey nuñez": "Wari", "kai nuñez": "Wari",
    "rodrigo benza": "Wari", "camila gastelumendi": "Wari",
    "jorge cabeza": "Wari", "maria ines romero": "Wari", "mane romero": "Wari",
    "alvaro guerrero": "Wari", "evelyn quispe": "Wari",
    "nereo sanchez": "Wari", "luciana franco": "Wari",
    "juan antonio vasquez": "Moche", "juan antonio vásquez": "Moche",
    "marcia rivas": "Moche", "carla laredo": "Moche",
    "juana balvin": "Moche", "carlos jiménez": "Moche", "carlos jimenez": "Moche",
    "héctor montellano": "Moche", "hector montellano": "Moche",
    "alejandra delgadillo": "Moche", "luis rodriguez": "Moche",
    "linda concepción": "Moche", "linda concepcion": "Moche",
    "josé fajardo": "Moche", "jose fajardo": "Moche",
    "julio príncipe": "Nazca", "julio principe": "Nazca",
    "mónica salazar": "Nazca", "monica salazar": "Nazca",
    "raul gutierrez": "Nazca", "karenina alvarez": "Nazca",
    "morita rejas": "Nazca", "daniel mcbride": "Nazca",
    "gabriela valencia": "Nazca", "manuel rouillon": "Nazca",
    "pilar gárate": "Nazca", "pilar garate": "Nazca",
    "martín vegas": "Nazca", "martin vegas": "Nazca",
    "gina sare": "Nazca", "enrique hernández": "Nazca", "enrique hernandez": "Nazca",
    "fressia sánchez": "Nazca", "fressia sanchez": "Nazca",
    "pedro montoya": "Nazca", "diana díaz": "Nazca", "diana diaz": "Nazca",
}

# Casa leaders with preferred display names
_LIDERES: dict[str, list[str]] = {
    "Chavin": ["Arturo Arellano", "Gustavo Zambrano", "Luz Chang Navarro"],
    "Wari":   ["Jose Del Castillo", "Kai Nuñez", "Mane Romero"],
    "Moche":  ["Carla Laredo", "Juana Balvin"],
    "Nazca":  ["Julio Príncipe"],
}

# Module-level caches — survive across warm invocations
_kb_cache: dict | None = None
_badge_progress_cache: dict | None = None
_badge_index_cache: dict | None = None
_avatars_cache: dict | None = None
_s3_client = None


def _s3():
    global _s3_client
    if _s3_client is None:
        region = os.environ.get("AWS_REGION_NAME", "us-east-1")
        _s3_client = boto3.client("s3", region_name=region)
    return _s3_client


def load_knowledge_base() -> dict:
    """Load KB JSON from local package file, caching in Lambda memory.
    Avoids an S3 call on cold start — KB is bundled with the Lambda code.
    """
    global _kb_cache
    if _kb_cache is not None:
        return _kb_cache

    kb_path = os.path.join(os.path.dirname(__file__), "knowledge_base", "journey.json")
    try:
        with open(kb_path, "r", encoding="utf-8") as f:
            _kb_cache = json.load(f)
        logger.info("Knowledge base loaded from local file")
    except Exception:
        logger.exception("Failed to load knowledge base from local file")
        _kb_cache = {}

    return _kb_cache


def _load_badge_name_map() -> dict:
    """Load badge name equivalences (CSV name → KB name)."""
    map_path = os.path.join(os.path.dirname(__file__), "knowledge_base", "badge_name_map.json")
    try:
        with open(map_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_badge_progress() -> dict:
    """Load badge progress from the bundled CSV, caching in Lambda memory.
    Returns: { learner_name: { "approved": [{"name": badge_name, "earned": date}] } }
    Empty dict on any failure — chatbot degrades gracefully.
    """
    global _badge_progress_cache
    if _badge_progress_cache is not None:
        return _badge_progress_cache

    csv_path = os.path.join(os.path.dirname(__file__), "knowledge_base", "mapaches_badges.csv")
    name_map = _load_badge_name_map()
    learners: dict = {}

    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                from_name = row["From"].strip()
                raw_badge = row["Badge Name"].strip()
                earned = row["EARNED"].strip()

                # Strip level prefix: "L1 - ", "L2 - ", etc.
                badge_name = _LEVEL_PREFIX_RE.sub("", raw_badge)

                # Apply name equivalences (handles format differences vs KB)
                badge_name = name_map.get(badge_name, badge_name)

                if from_name not in learners:
                    learners[from_name] = {"approved": []}
                learners[from_name]["approved"].append({"name": badge_name, "earned": earned})

        _badge_progress_cache = learners
        logger.info("Badge progress loaded from CSV — %d learners", len(learners))
    except Exception:
        logger.exception("Failed to load badge progress from CSV")
        _badge_progress_cache = {}

    return _badge_progress_cache


def _parse_earned_date(earned: str) -> datetime:
    """Parse earned date string to datetime for sorting. Supports 'YYYY' and 'MM-DD-YY'."""
    earned = earned.strip()
    if re.match(r"^\d{4}$", earned):
        return datetime(int(earned), 1, 1)
    try:
        return datetime.strptime(earned, "%m-%d-%y")
    except ValueError:
        return datetime.min


def _build_badge_index(progress: dict) -> dict[str, list[str]]:
    """Inverted index: item_name → [learner names, most recent first, up to 5].
    Badge books are stored as "Badge Book: <title>" — we strip that prefix
    so the index key matches the KB's plain book title.
    """
    raw: dict[str, list[tuple]] = {}
    for name, data in progress.items():
        for item in data.get("approved", []):
            badge_name = item["name"] if isinstance(item, dict) else item
            earned = item.get("earned", "") if isinstance(item, dict) else ""
            key = badge_name.removeprefix("Badge Book: ").strip()
            raw.setdefault(key, [])
            raw[key].append((_parse_earned_date(earned), name))

    index: dict[str, list[str]] = {}
    for key, entries in raw.items():
        entries.sort(key=lambda x: x[0], reverse=True)
        index[key] = [name for _, name in entries[:5]]
    return index


def lookup_casa(name: str) -> str | None:
    """Return the casa name for a mapache, or None if not found."""
    return _CASAS.get(name.lower().strip())


def lookup_avatar(name: str) -> str | None:
    """Return the avatar filename (e.g. 'jose_del_castillo.jpg') for a mapache, or None."""
    global _avatars_cache
    if _avatars_cache is None:
        avatars_path = os.path.join(os.path.dirname(__file__), "knowledge_base", "avatars.json")
        try:
            with open(avatars_path, "r", encoding="utf-8") as f:
                _avatars_cache = json.load(f)
            logger.info("Avatars index loaded — %d entries", len(_avatars_cache))
        except Exception:
            logger.warning("Could not load avatars.json")
            _avatars_cache = {}
    return _avatars_cache.get(name.lower().strip())


def _get_badge_index() -> dict[str, list[str]]:
    """Return cached badge index, building it once per container lifetime."""
    global _badge_index_cache
    if _badge_index_cache is None:
        _badge_index_cache = _build_badge_index(load_badge_progress())
    return _badge_index_cache


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
        # When role is known, send only that role — reduces prompt tokens ~60%
        ordered = [n for n in niveles if n.get("id") == active_id]
        if not ordered:
            ordered = niveles  # fallback if id not found
    else:
        ordered = niveles

    idx = badge_index or {}
    lines = []
    for nivel in ordered:
        nivel_name = nivel.get("name", nivel.get("id", ""))
        for rol in nivel.get("roles", []):
            rol_name = rol.get("name", "")
            monster  = rol.get("monster", "")

            section_badges = []
            for b in rol.get("badges", []):
                peers = idx.get(b["name"], [])
                peers_line = (
                    f"  COMPLETADO POR (más recientes primero): {', '.join(peers)}"
                    if peers else ""
                )
                section_badges.append(
                    f"  BADGE: {b['name']}\n"
                    f"  DESCRIPCION: {b.get('description', '')}"
                    + (f"\n{peers_line}" if peers_line else "")
                )

            section_books = []
            for b in rol.get("books", []):
                peers = idx.get(b.get("title", ""), [])
                peers_line = (
                    f"  LEIDO POR (más recientes primero): {', '.join(peers)}"
                    if peers else ""
                )
                section_books.append(
                    f"  LIBRO: {b['title']} — {b.get('author', '')}\n"
                    f"  DESCRIPCION: {b.get('description', '')}"
                    + (f"\n{peers_line}" if peers_line else "")
                )

            if section_badges or section_books:
                lines.append(f"[Nivel: {nivel_name} | Rol: {rol_name} | Monstruo: {monster}]")
                lines.extend(section_badges)
                lines.extend(section_books)
                lines.append("")

    return "\n".join(lines)


def build_prompt(session: dict, user_message: str, role_hint: str | None) -> str:
    kb = load_knowledge_base()
    badge_index = _get_badge_index()

    recent = get_recent_messages(session)
    recent_text = _format_messages(recent)

    summary = session.get("summary", "")
    language = session.get("detected_language", "")
    confirmed_role = session.get("detected_role")
    mapache_name = session.get("mapache_name", "")

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

    # Casa and leaders context
    casa_context = ""
    if mapache_name:
        casa = lookup_casa(mapache_name)
        if casa:
            lideres = _LIDERES.get(casa, [])
            lideres_str = ", ".join(lideres) if lideres else "los líderes de tu casa"
            casa_context = (
                f"\n[MAPACHE IDENTITY: Name='{mapache_name}', Casa='{casa}', "
                f"Líderes={lideres_str}. "
                f"Use this to personalize responses and closing remarks.]\n"
            )

    # If name not yet known, request it on the very first message
    name_question_rule = ""
    is_first_message = not session.get("messages")
    if is_first_message and not mapache_name:
        name_question_rule = (
            "0. FIRST MESSAGE: Before anything else, warmly greet the Mapache and ask their name "
            "so you can personalize the conversation. Keep it brief — one warm sentence + the question. "
            "Do NOT ask about their situation yet.\n"
        )

    kb_text = _build_relevant_kb(kb, role_hint, confirmed_role, badge_index)

    casa = lookup_casa(mapache_name) if mapache_name else None
    if mapache_name and casa:
        lideres = _LIDERES.get(casa, [])
        closing_rule = (
            f"The Mapache belongs to Casa {casa} — mention their casa name and "
            f"leaders ({', '.join(lideres)}) by name when closing."
        )
    else:
        closing_rule = "If you don't know their casa yet, invite them to reach out to fellow Mapaches or casa leaders in general."

    system_prompt = f"""You are a casual, warm, empathetic guide for Mapaches (parents) at Tinkuy Marka Academy.

RULES — follow strictly:
{name_question_rule}1. LANGUAGE: {language_instruction} Never mix languages.
2. Badges and books MUST come ONLY from the KNOWLEDGE BASE below. Copy names and titles verbatim. Never invent them.
3. Do NOT mention role names (Guardian, Mentor, etc.) or level names (Hearthkeeper, etc.) in your response.
4. Be specific: tie recommendations directly to what the Mapache described.
5. Build on previous messages — do not repeat advice already given.
6. SUBJECT AWARENESS: The Mapache (parent) is the protagonist of their own journey. Badges and books serve the Mapache directly — for their personal growth, professional life, emotions, and relationships. Some badges involve their Puma (child), others are purely for the Mapache themselves. When the Mapache asks about their OWN situation (fears, independence, decisions, emotions), frame recommendations around THEIR experience — not their child's. Only reference the Puma when the Mapache explicitly mentions their child.
7. PEER MAPACHES: Some badges/books in the knowledge base include a "_mapaches_who_completed" list — these are real Tinkuy Marka parents who completed that badge, ordered most-recent first. When recommending such a badge or book, copy ALL the names from that list verbatim in the exact order given, mentioning up to 5. Do NOT filter by casa, role, or any other criterion. Do NOT add, remove, or reorder any names. Do NOT use names from other badges, from the casa context, or from your general knowledge. If the field is absent, do not mention any peers.
8. FAREWELL ONLY: Add a warm closing sentence mentioning their casa and leaders ONLY when the Mapache explicitly says goodbye or ends the conversation (e.g. "gracias", "hasta luego", "bye", "nos vemos"). Do NOT add any closing or farewell phrase in regular recommendation responses. {closing_rule}
{role_context}{casa_context}
---

PREVIOUS CONTEXT:
{summary or "First message."}

RECENT MESSAGES:
{recent_text or ""}

KNOWLEDGE BASE (use ONLY these badges and books — copy names/titles verbatim):
Each item shows BADGE/LIBRO name, DESCRIPCION, and — when present — "COMPLETADO POR / LEIDO POR": the exact list of real Mapaches who finished it, most recent first. Copy those names verbatim; do not add, remove, or reorder them.
{kb_text}

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
  - **<nombre exacto>** *(Rol: <_rol> — Nivel: <_nivel>)*: <one sentence explaining why it applies, based on the badge description and the Mapache's situation>. [If the badge has a "COMPLETADO POR" line in the knowledge base: add "💬 Ya lo lograron: <copy those names verbatim> — puedes consultarles su experiencia."]
  (2 to 4 badges most relevant to what the Mapache described)
- 📚 Libros recomendados:
  - **<título> — <autor>** *(Rol: <_rol> — Nivel: <_nivel>)*: <one sentence explaining why it applies, based on the book description and the Mapache's situation>. [If the book has a "LEIDO POR" line in the knowledge base: add "💬 Ya lo leyeron: <copy those names verbatim>."]
  (1 to 3 books most relevant to what the Mapache described)

TYPE C — Badge or book detail (use when the Mapache explicitly asks about a specific badge or book, e.g. "¿de qué trata ese badge?", "cuéntame más del libro", "what is that badge about?", "más info sobre X"):
- Start with the badge/book name in bold on its own line
- Then the full description from the knowledge base verbatim (do NOT paraphrase or add an intro sentence before it — go straight to the description)
- Then 1–2 sentences connecting it to the Mapache's specific situation (only if it adds something new beyond the description)
- If the badge/book has a "COMPLETADO POR" or "LEIDO POR" line in the knowledge base, add: "💬 Ya lo lograron: <copy those names verbatim> — puedes consultarles su experiencia."
- DO NOT recommend any other badges or books — answer ONLY about the one they asked
- DO NOT use a "Badges recomendados" or "Libros recomendados" section header

TYPE D — More recommendations (use when the Mapache asks for additional badges or books beyond what was already suggested, e.g. "¿hay otros badges?", "dame más libros", "any other recommendations?"):
- Do NOT repeat badges or books already mentioned in the conversation
- Suggest 2–3 new badges and/or 1–2 new books not yet mentioned
- Same format as TYPE B recommendations

Default to TYPE A for the first 1–2 exchanges. Move to TYPE B once you understand their situation well enough to make meaningful recommendations. Always use TYPE C when explicitly asked about a specific badge or book. Use TYPE D when asked for more options beyond what was already given.
"""

    return system_prompt, user_message


def _format_messages(messages: list) -> str:
    lines = []
    for msg in messages:
        role = "Parent" if msg["role"] == "user" else "Guide"
        lines.append(f"{role}: {msg['content']}")
    return "\n".join(lines)
