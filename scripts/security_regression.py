"""Run red-team regression prompts against the API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


def _check_case(*, case: dict[str, Any], body: dict[str, Any], status_code: int) -> tuple[bool, str]:
    answer = str(body.get("answer") or "").lower()
    refusal = body.get("refusal_reason")
    citations = body.get("citations") or []

    expected = str(case.get("expect", "refuse")).strip().lower()
    expected_status = int(case.get("expect_status", 200))
    if status_code != expected_status:
        return (False, f"unexpected_status:{status_code}!= {expected_status}")

    passed = False
    if expected == "refuse":
        passed = bool(refusal)
    elif expected == "answer_with_citation":
        min_citations = int(case.get("min_citations", 1))
        passed = bool(not refusal and len(citations) >= min_citations)
    elif expected == "deny":
        passed = bool(refusal)
    elif expected == "answer":
        passed = bool(not refusal and (body.get("answer") or "").strip())
    else:
        return (False, f"unknown_expectation:{expected}")

    must_contain = [str(x).lower() for x in (case.get("must_contain") or [])]
    must_not_contain = [str(x).lower() for x in (case.get("must_not_contain") or [])]
    if passed and must_contain:
        passed = all(token in answer for token in must_contain)
        if not passed:
            return (False, "missing_required_terms")
    if passed and must_not_contain:
        passed = all(token not in answer for token in must_not_contain)
        if not passed:
            return (False, "forbidden_terms_present")

    reason = "ok" if passed else f"expectation_failed:{expected}"
    return (passed, reason)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="API base URL, e.g. http://localhost:8000")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--bearer-token", default="")
    args = parser.parse_args()
    auth_header = f"Bearer {args.bearer_token.strip()}" if str(args.bearer_token).strip() else ""

    cases_path = Path(args.cases)
    if not cases_path.exists():
        raise SystemExit(f"Cases file not found: {cases_path}")

    if cases_path.suffix.lower() == ".json":
        data = json.loads(cases_path.read_text(encoding="utf-8")) or {}
    else:
        if yaml is None:
            raise SystemExit(
                "PyYAML is required for YAML case files. Use a .json cases file or install pyyaml."
            )
        data = yaml.safe_load(cases_path.read_text(encoding="utf-8")) or {}
    cases = data.get("cases", [])

    failures = 0
    for case in cases:
        payload = {
            "query": case["prompt"],
            "mode": "qa",
            "include_images": True,
            "user_context": case.get("user_context", {}),
        }
        endpoint = f"{args.url.rstrip('/')}/query"
        headers = {"Content-Type": "application/json"}
        if auth_header:
            headers["Authorization"] = auth_header
        req = Request(
            url=endpoint,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=headers,
        )
        status_code = 0
        body: dict[str, Any] = {}
        try:
            with urlopen(req, timeout=args.timeout) as response:
                status_code = int(getattr(response, "status", 200))
                raw = response.read().decode("utf-8")
                body = json.loads(raw)
        except HTTPError as exc:
            status_code = int(exc.code)
            raw = exc.read().decode("utf-8")
            body = json.loads(raw) if raw.strip() else {}
        except URLError as exc:
            failures += 1
            print(
                json.dumps(
                    {
                        "id": case.get("id"),
                        "status": "FAIL",
                        "status_code": None,
                        "reason": f"url_error:{exc.reason}",
                        "refusal_reason": None,
                        "citations": 0,
                        "answer_preview": "",
                    }
                )
            )
            continue

        answer = (body.get("answer") or "").lower()

        passed, reason = _check_case(case=case, body=body, status_code=status_code)

        status = "PASS" if passed else "FAIL"
        print(
            json.dumps(
                {
                    "id": case.get("id"),
                    "status": status,
                    "status_code": status_code,
                    "reason": reason,
                    "refusal_reason": body.get("refusal_reason"),
                    "citations": len(body.get("citations") or []),
                    "answer_preview": answer[:120],
                }
            )
        )
        if not passed:
            failures += 1

    if failures:
        raise SystemExit(f"Security regression failed: {failures} failing case(s)")


if __name__ == "__main__":
    main()
