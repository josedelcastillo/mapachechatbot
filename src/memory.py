"""
DynamoDB session management.
Schema:
  session_id (PK)   str
  messages          list[{role, content}]
  summary           str
  detected_role     str | None
  detected_language str | None
  updated_at        int (epoch)
  ttl               int (epoch, 7 days)
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from summarizer import generate_summary

logger = logging.getLogger()

TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]
SHORT_TERM_LIMIT = 6       # messages kept in recent context
SUMMARY_EVERY_N = 10       # update summary every N messages
DAILY_MESSAGE_LIMIT = 20   # max user messages per session per day

_dynamodb = None


def _table():
    global _dynamodb
    if _dynamodb is None:
        region = os.environ.get("AWS_REGION_NAME", "us-east-1")
        _dynamodb = boto3.resource("dynamodb", region_name=region).Table(TABLE_NAME)
    return _dynamodb


def load_session(session_id: str) -> dict:
    """Return session dict or empty skeleton."""
    try:
        resp = _table().get_item(Key={"session_id": session_id})
        item = resp.get("Item")
        if item:
            item["messages"] = item.get("messages", [])
            return item
    except Exception:
        logger.exception("Error loading session %s", session_id)

    return {
        "session_id": session_id,
        "messages": [],
        "summary": "",
        "detected_role": None,
        "detected_language": None,
        "mapache_name": None,
    }


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def check_daily_limit(session: dict) -> bool:
    """Return True if the session has reached the daily message limit."""
    today = _today()
    if session.get("daily_date") != today:
        return False
    return int(session.get("daily_count", 0)) >= DAILY_MESSAGE_LIMIT


def save_message(
    session_id: str,
    session: dict,
    user_message: str,
    assistant_response: str,
) -> None:
    """Append user + assistant messages and persist session."""
    messages: list = session.get("messages", [])
    messages.append({"role": "user", "content": user_message})
    messages.append({"role": "assistant", "content": assistant_response})
    session["messages"] = messages

    today = _today()
    if session.get("daily_date") != today:
        session["daily_date"] = today
        session["daily_count"] = 1
    else:
        session["daily_count"] = int(session.get("daily_count", 0)) + 1

    now = int(time.time())
    ttl = now + 7 * 24 * 3600  # 7 days

    try:
        _table().put_item(Item={
            "session_id": session_id,
            "messages": messages,
            "summary": session.get("summary", ""),
            "detected_role": session.get("detected_role"),
            "detected_language": session.get("detected_language"),
            "mapache_name": session.get("mapache_name"),
            "daily_date": session["daily_date"],
            "daily_count": session["daily_count"],
            "updated_at": now,
            "ttl": ttl,
        })
    except Exception:
        logger.exception("Error saving session %s", session_id)


def maybe_update_summary(session_id: str, session: dict) -> None:
    """Regenerate summary every SUMMARY_EVERY_N messages.

    Also updates detected_role if the summarizer returns a more confident
    role identification than what was previously stored.
    """
    messages = session.get("messages", [])
    if len(messages) % SUMMARY_EVERY_N != 0:
        return

    try:
        result = generate_summary(messages, session.get("summary", ""))
        new_summary = result["summary"]
        new_role = result["detected_role"]

        update_expr = "SET summary = :s"
        expr_values = {":s": new_summary}

        # Only overwrite detected_role when the summarizer is confident
        # (i.e., returns a non-null value). Never clear a previously confirmed role.
        if new_role:
            update_expr += ", detected_role = :r"
            expr_values[":r"] = new_role
            session["detected_role"] = new_role

        _table().update_item(
            Key={"session_id": session_id},
            UpdateExpression=update_expr,
            ExpressionAttributeValues=expr_values,
        )
        session["summary"] = new_summary
        logger.info(
            "Summary updated for session %s (detected_role=%s)", session_id, new_role
        )
    except Exception:
        logger.exception("Error updating summary for session %s", session_id)


def get_recent_messages(session: dict) -> list:
    """Return last SHORT_TERM_LIMIT messages."""
    messages = session.get("messages", [])
    return messages[-SHORT_TERM_LIMIT:]
