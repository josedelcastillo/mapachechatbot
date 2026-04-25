"""
Amazon Bedrock — Claude Haiku invocation.
Uses the Messages API via bedrock-runtime.
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

_bedrock_client = None


def _client():
    global _bedrock_client
    if _bedrock_client is None:
        region = os.environ.get("AWS_REGION_NAME", "us-east-1")
        _bedrock_client = boto3.client("bedrock-runtime", region_name=region)
    return _bedrock_client


def invoke_claude(system_prompt: str, user_message: str, max_tokens: int = 1024) -> str:
    """
    Send a prompt to Claude Haiku and return the text response.
    Uses the Anthropic Messages API format via Bedrock with a proper system prompt.
    """
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": user_message},
        ],
    }

    try:
        response = _client().invoke_model(
            modelId=MODEL_ID,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(body),
        )
        result = json.loads(response["body"].read())
        return result["content"][0]["text"]
    except Exception:
        logger.exception("Bedrock invocation failed")
        raise
