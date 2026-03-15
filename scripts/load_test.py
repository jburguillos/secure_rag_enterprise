"""Simple load test script for /query endpoint."""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import statistics
import threading
import time
from typing import Any
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class AuthTokenProvider:
    """Thread-safe token provider with lazy refresh via Keycloak direct grant."""

    def __init__(
        self,
        *,
        bearer_token: str,
        token_url: str,
        client_id: str,
        username: str,
        password: str,
        timeout: int,
    ) -> None:
        self._static_bearer = bearer_token.strip()
        self._token_url = token_url.strip()
        self._client_id = client_id.strip()
        self._username = username.strip()
        self._password = password.strip()
        self._timeout = timeout
        self._token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    @property
    def can_refresh(self) -> bool:
        return bool(self._token_url and self._client_id and self._username and self._password)

    def _fetch(self) -> tuple[str, int]:
        body = urlencode(
            {
                "grant_type": "password",
                "client_id": self._client_id,
                "username": self._username,
                "password": self._password,
            }
        ).encode("utf-8")
        req = Request(
            url=self._token_url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urlopen(req, timeout=self._timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        token = str(payload.get("access_token") or "").strip()
        expires_in = int(payload.get("expires_in") or 300)
        if not token:
            raise RuntimeError("token endpoint did not return access_token")
        return token, max(30, expires_in)

    def get_header(self, *, force_refresh: bool = False) -> str | None:
        if self._static_bearer and not self.can_refresh:
            return f"Bearer {self._static_bearer}"

        if not self.can_refresh:
            return None

        now = time.time()
        with self._lock:
            if force_refresh or not self._token or now >= (self._expires_at - 30):
                token, expires_in = self._fetch()
                self._token = token
                self._expires_at = now + expires_in
            return f"Bearer {self._token}"


def one_call(
    url: str,
    payload: dict[str, Any],
    timeout: int,
    auth_provider: AuthTokenProvider | None,
) -> tuple[bool, float, int | None, str | None]:
    started = time.perf_counter()

    def _attempt(*, force_refresh: bool = False) -> tuple[bool, float, int | None, str | None]:
        auth_header = auth_provider.get_header(force_refresh=force_refresh) if auth_provider else None
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if auth_header:
            headers["Authorization"] = auth_header
        req = Request(url=url, data=body, method="POST", headers=headers)
        try:
            with urlopen(req, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                status = int(getattr(response, "status", 200))
            _ = json.loads(raw)
            elapsed = time.perf_counter() - started
            if status >= 400:
                return (False, elapsed, status, f"http_{status}")
            return (True, elapsed, status, None)
        except HTTPError as exc:
            elapsed = time.perf_counter() - started
            return (False, elapsed, int(exc.code), f"http_{exc.code}")
        except URLError as exc:
            elapsed = time.perf_counter() - started
            return (False, elapsed, None, f"url_error:{exc.reason}")
        except Exception as exc:
            elapsed = time.perf_counter() - started
            return (False, elapsed, None, str(exc))

    ok, elapsed, status, error = _attempt()
    if not ok and status == 401 and auth_provider and auth_provider.can_refresh:
        return _attempt(force_refresh=True)
    return (ok, elapsed, status, error)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    idx = int(round((p / 100.0) * (len(values) - 1)))
    return sorted(values)[idx]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--max-failure-rate", type=float, default=0.05)
    parser.add_argument("--query", default="Summarize the key compliance controls")
    parser.add_argument("--include-images", action="store_true")
    parser.add_argument("--retrieval-mode", choices=["auto", "rag", "chat"], default="auto")
    parser.add_argument("--bearer-token", default="")
    parser.add_argument("--token-url", default="")
    parser.add_argument("--client-id", default="secure-rag-api")
    parser.add_argument("--username", default="")
    parser.add_argument("--password", default="")
    args = parser.parse_args()
    auth_provider = AuthTokenProvider(
        bearer_token=args.bearer_token,
        token_url=args.token_url,
        client_id=args.client_id,
        username=args.username,
        password=args.password,
        timeout=args.timeout,
    )

    payload = {
        "query": args.query,
        "mode": "qa",
        "retrieval_mode": args.retrieval_mode,
        "include_images": bool(args.include_images),
        "user_context": {"email": "hr.user@example.com", "domain": "example.com", "groups": ["hr"]},
    }

    all_latencies: list[float] = []
    success_latencies: list[float] = []
    failed: list[tuple[int | None, str | None]] = []
    started = time.perf_counter()
    with futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        work = [
            executor.submit(one_call, args.url, payload, args.timeout, auth_provider)
            for _ in range(args.requests)
        ]
        for item in futures.as_completed(work):
            ok, elapsed, status_code, error = item.result()
            all_latencies.append(elapsed)
            if ok:
                success_latencies.append(elapsed)
            else:
                failed.append((status_code, error))
    elapsed = time.perf_counter() - started

    throughput = len(all_latencies) / elapsed if elapsed else 0.0
    success = len(success_latencies)
    failure_count = len(failed)
    failure_rate = (failure_count / len(all_latencies)) if all_latencies else 0.0

    print(f"total_requests={len(all_latencies)}")
    print(f"success_requests={success}")
    print(f"failed_requests={failure_count}")
    print(f"failure_rate={failure_rate:.4f}")
    print(f"elapsed_seconds={elapsed:.3f}")
    print(f"throughput_rps={throughput:.3f}")
    print(f"p50_latency_s={percentile(success_latencies, 50):.3f}")
    print(f"p95_latency_s={percentile(success_latencies, 95):.3f}")
    if success_latencies:
        print(f"avg_latency_s={statistics.mean(success_latencies):.3f}")
    else:
        print("avg_latency_s=0.000")

    if failed:
        sample = failed[:5]
        print("failure_samples=" + ", ".join([f"status={s},error={e}" for s, e in sample]))

    if failure_rate > args.max_failure_rate:
        raise SystemExit(
            f"Load test failure rate {failure_rate:.4f} exceeds threshold {args.max_failure_rate:.4f}"
        )


if __name__ == "__main__":
    main()
