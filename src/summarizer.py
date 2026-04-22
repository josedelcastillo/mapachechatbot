"""
Conversation summarizer.
Generates a compact summary every N messages to maintain long-term context
without blowing up the token budget.
Returns both the summary text and the detected Hero's Journey role.
"""

import json
import logging
import os

import boto3

logger = logging.getLogger()

MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID",
    "anthropic.claude-3-haiku-20240307-v1:0",
)

_VALID_ROLES = {"guardian", "mentor", "challenger", "ally", "legacy"}

_bedrock_client = None


def _client():
    global _bedrock_client
    if _bedrock_client is None:
        region = os.environ.get("AWS_REGION_NAME", "us-east-1")
        _bedrock_client = boto3.client("bedrock-runtime", region_name=region)
    return _bedrock_client


def generate_summary(messages: list, previous_summary: str = "") -> dict:
    """
    Generate a concise summary of the conversation and detect the Mapache's
    current Hero's Journey role.

    Returns:
        dict with keys:
          - "summary": str  (max 120 words, in English)
          - "detected_role": str | None  (guardian|mentor|challenger|ally|legacy or null)
    """
    conversation_text = "\n".join(
        f"{'Parent' if m['role'] == 'user' else 'Guide'}: {m['content']}"
        for m in messages
    )

    prompt = f"""Analyze this conversation between a parent (Mapache) and a guide at Tinkuy Marka Academy.

Previous summary (incorporate this context):
{previous_summary or "None"}

Conversation:
{conversation_text}

Return ONLY a valid JSON object with exactly these two keys:

{{
  "summary": "<summary in English, max 120 words covering: Mapache's main concerns and emotional state, Puma's behaviors mentioned, Hero's Journey role identified, monsters/fears identified, guidance given so far, language used>",
  "detected_role": "<one of: guardian, mentor, challenger, ally, legacy — or null if not yet clear>"
}}

Rules:
- "summary" must be factual and specific, max 120 words.
- "detected_role" must be the single best-matching role from the five options, or null.
- Output ONLY the JSON object. No explanation, no markdown, no extra text."""

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 300,
        "messages": [{"role": "user", "content": prompt}],
    }

    fallback = {"summary": previous_summary, "detected_role": None}

    try:
        response = _client().invoke_model(
            modelId=MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
        raw = json.loads(response["body"].read())["content"][0]["text"].strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        parsed = json.loads(raw)
        summary_text = parsed.get("summary", previous_summary) or previous_summary
        role = parsed.get("detected_role")
        if isinstance(role, str):
            role = role.lower().strip()
        detected_role = role if role in _VALID_ROLES else None

        return {"summary": summary_text, "detected_role": detected_role}

    except Exception:
        logger.exception("Summary generation failed")
        return fallback
