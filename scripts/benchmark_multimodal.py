"""Run a tiny benchmark subset for multimodal vs text-only retrieval."""

from __future__ import annotations

import argparse
import json

import requests
import yaml


def ask(api_url: str, query: str, include_images: bool) -> dict:
    payload = {
        "query": query,
        "mode": "qa",
        "include_images": include_images,
        "user_context": {"email": "hr.user@example.com", "domain": "example.com", "groups": ["hr"]},
    }
    response = requests.post(f"{api_url.rstrip('/')}/query", json=payload, timeout=120)
    response.raise_for_status()
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--dataset", default="tests/redteam/prompts.yaml")
    args = parser.parse_args()

    data = yaml.safe_load(open(args.dataset, "r", encoding="utf-8")) or {}
    questions = [c["prompt"] for c in data.get("cases", [])[:5]]

    results = []
    for question in questions:
        text_only = ask(args.api_url, question, include_images=False)
        multimodal = ask(args.api_url, question, include_images=True)
        results.append(
            {
                "question": question,
                "text_only_citations": len(text_only.get("citations", [])),
                "multimodal_citations": len(multimodal.get("citations", [])),
                "text_only_refusal": text_only.get("refusal_reason"),
                "multimodal_refusal": multimodal.get("refusal_reason"),
            }
        )

    print(json.dumps({"results": results}, indent=2))


if __name__ == "__main__":
    main()
