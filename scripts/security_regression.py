"""Run red-team regression prompts against the API."""

from __future__ import annotations

import argparse
import json

import requests
import yaml


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="API base URL, e.g. http://localhost:8000")
    parser.add_argument("--cases", required=True)
    args = parser.parse_args()

    data = yaml.safe_load(open(args.cases, "r", encoding="utf-8")) or {}
    cases = data.get("cases", [])

    failures = 0
    for case in cases:
        payload = {
            "query": case["prompt"],
            "mode": "qa",
            "include_images": True,
            "user_context": case.get("user_context", {}),
        }
        response = requests.post(f"{args.url.rstrip('/')}/query", json=payload, timeout=120)
        body = response.json()
        answer = (body.get("answer") or "").lower()
        refusal = body.get("refusal_reason")

        expected = case.get("expect", "refuse")
        passed = False
        if expected == "refuse":
            passed = bool(refusal)
        elif expected == "answer_with_citation":
            passed = bool(body.get("citations")) and not refusal
        elif expected == "deny":
            passed = bool(refusal)

        status = "PASS" if passed else "FAIL"
        print(json.dumps({"id": case.get("id"), "status": status, "refusal_reason": refusal, "answer_preview": answer[:120]}))
        if not passed:
            failures += 1

    if failures:
        raise SystemExit(f"Security regression failed: {failures} failing case(s)")


if __name__ == "__main__":
    main()
