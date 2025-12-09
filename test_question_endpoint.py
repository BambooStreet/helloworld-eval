#!/usr/bin/env python3
"""Utility script to send sample payloads to the local /api/question endpoint."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

DEFAULT_URL = "http://localhost:7071/api/question"


def load_json(path: Path, expected_type: type) -> Any:
    """Load JSON from *path* and validate the top-level type."""
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, expected_type):
        raise ValueError(f"Expected {expected_type.__name__} root in {path}")
    return data


def default_conversation() -> List[Dict[str, str]]:
    """Return a tiny fallback conversation payload for smoke tests."""
    return [
        {"speaker": "human", "utterance": "Hi there, tell me about the service."},
        {"speaker": "ai", "utterance": "Hello! What would you like to know?"},
    ]


def build_payload(args: argparse.Namespace) -> Dict[str, Any]:
    """Assemble the request payload using CLI args."""
    payload: Dict[str, Any] = {}

    if args.conversation_file:
        payload["Conversation"] = load_json(args.conversation_file, list)
    else:
        payload["Conversation"] = default_conversation()

    if args.query:
        payload["query"] = args.query

    if args.mongo_query_file:
        payload["mongo_query"] = load_json(args.mongo_query_file, list)

    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send a JSON request to the local Azure Functions question endpoint."
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Question endpoint URL (default: {DEFAULT_URL})",
    )
    parser.add_argument(
        "--query",
        help="Explicit query text. If omitted, the last human utterance is used.",
    )
    parser.add_argument(
        "--conversation-file",
        type=Path,
        help="Path to JSON file containing a Conversation array.",
    )
    parser.add_argument(
        "--mongo-query-file",
        type=Path,
        help="Optional JSON file describing a mongo_query list to include in the payload.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Request timeout in seconds (default: 30).",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Path to .env file (defaults to autodetect)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.env_file:
        if not args.env_file.exists():
            print(f".env file not found: {args.env_file}", file=sys.stderr)
            return 1
        load_dotenv(dotenv_path=args.env_file)
    else:
        load_dotenv()

    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY missing. Please set it in .env or pass --env-file.", file=sys.stderr)
        return 1

    payload = build_payload(args)

    try:
        response = requests.post(args.url, json=payload, timeout=args.timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Request failed: {exc}", file=sys.stderr)
        if exc.response is not None:
            print(f"Response body: {exc.response.text}", file=sys.stderr)
        return 1

    print("=== Response ===")
    try:
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
    except ValueError:
        # Log raw text if JSON decoding fails.
        print(response.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
