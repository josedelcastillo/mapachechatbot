"""
Lambda entry point for the Mapache Chatbot.
Receives: { "session_id": "...", "message": "..." }
Returns:  { "response": "...", "session_id": "..." }
"""

import json
import logging

from memory import load_session, save_message, maybe_update_summary
from rag import build_prompt, detect_role_hint, detect_language
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

        # Build prompt with context + knowledge base
        prompt = build_prompt(
            session=session,
            user_message=user_message,
            role_hint=role_hint,
        )

        # Call Claude Haiku via Bedrock
        assistant_response = invoke_claude(prompt)

        # Persist messages and optionally update summary
        save_message(session_id, session, user_message, assistant_response)
        maybe_update_summary(session_id, session)

        return _response(200, {
            "session_id": session_id,
            "response": assistant_response,
        })

    except ValueError as e:
        logger.warning("Validation error: %s", e)
        return _response(400, {"error": str(e)})
    except Exception as e:
        logger.exception("Unexpected error")
        return _response(500, {"error": "Internal server error"})


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
