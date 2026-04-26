"""
Lambda entry point for the Mapache Chatbot.
Receives: { "session_id": "...", "message": "..." }
Returns:  { "response": "...", "session_id": "..." }
"""

import json
import logging

from memory import load_session, save_message, maybe_update_summary, check_daily_limit, DAILY_MESSAGE_LIMIT
from rag import build_prompt, detect_role_hint, detect_language, lookup_casa, lookup_avatar
from bedrock import invoke_claude

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: dict, context) -> dict:
    try:
        body = _parse_body(event)
        session_id = body.get("session_id", "").strip()
        user_message = body.get("message", "").strip()

        if not session_id or not user_message:
            return _response(400, {"error": "session_id and message are required"})

        # Load existing session from DynamoDB
        session = load_session(session_id)

        # Detect role hint from keywords (heuristic layer)
        role_hint = detect_role_hint(user_message)
        if role_hint and not session.get("detected_role"):
            session["detected_role"] = role_hint

        # Detect language from user message (update if first message or language changed)
        language = detect_language(user_message)
        if language:
            session["detected_language"] = language

        # Detect mapache name from user message when not yet known.
        # We check if the message is a short reply (likely answering "what's your name?")
        # and if it matches a known mapache in the casa registry.
        if not session.get("mapache_name"):
            detected_name = _detect_mapache_name(user_message)
            if detected_name:
                session["mapache_name"] = detected_name

        # Enforce daily message limit
        if check_daily_limit(session):
            lang = session.get("detected_language", "es")
            if lang == "en":
                limit_msg = (
                    f"You've reached the {DAILY_MESSAGE_LIMIT}-message daily limit for this session. "
                    "Come back tomorrow to continue your Hero's Journey! 🦝"
                )
            else:
                limit_msg = (
                    f"Has alcanzado el límite de {DAILY_MESSAGE_LIMIT} mensajes diarios para esta sesión. "
                    "¡Vuelve mañana para continuar tu Viaje del Héroe! 🦝"
                )
            return _response(200, {"session_id": session_id, "response": limit_msg})

        # Build prompt with context + knowledge base
        system_prompt, user_content = build_prompt(
            session=session,
            user_message=user_message,
            role_hint=role_hint,
        )

        # Call Claude Haiku via Bedrock
        assistant_response = invoke_claude(system_prompt, user_content)

        # Persist messages and optionally update summary
        save_message(session_id, session, user_message, assistant_response)
        maybe_update_summary(session_id, session)

        # Resolve avatar — include in response so the frontend can display the
        # mapache's photo immediately after their name is identified.
        mapache_name = session.get("mapache_name")
        avatar_file = lookup_avatar(mapache_name) if mapache_name else None

        result: dict = {
            "session_id": session_id,
            "response": assistant_response,
        }
        if mapache_name:
            result["mapache_name"] = mapache_name
        if avatar_file:
            result["avatar_url"] = f"mapache-fotos/{avatar_file}"

        return _response(200, result)

    except ValueError as e:
        logger.warning("Validation error: %s", e)
        return _response(400, {"error": str(e)})
    except Exception as e:
        logger.exception("Unexpected error")
        return _response(500, {"error": "Internal server error"})


def _detect_mapache_name(message: str) -> str | None:
    """Try to match the user message against known mapache names.
    Checks substrings so partial names like 'Jose' or 'Kailey' are caught.
    Returns the canonical name (as stored in _CASAS keys, title-cased) or None.
    """
    from rag import _CASAS
    lower = message.lower().strip()
    # Try longest matches first to avoid 'jose' matching 'jose fajardo' before 'jose del castillo'
    candidates = sorted(_CASAS.keys(), key=len, reverse=True)
    for candidate in candidates:
        if candidate in lower:
            return candidate.title()
    return None


def _parse_body(event: dict) -> dict:
    body = event.get("body", event)
    if isinstance(body, str):
        body = json.loads(body)
    return body or {}


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, ensure_ascii=False),
    }
