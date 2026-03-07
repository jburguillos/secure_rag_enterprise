"""Simple load test script for /query endpoint."""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import statistics
import time

import requests


def one_call(url: str, payload: dict) -> float:
    started = time.perf_counter()
    response = requests.post(url, json=payload, timeout=120)
    response.raise_for_status()
    _ = response.json()
    return time.perf_counter() - started


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
    parser.add_argument("--query", default="Summarize the key compliance controls")
    args = parser.parse_args()

    payload = {
        "query": args.query,
        "mode": "qa",
        "include_images": True,
        "user_context": {"email": "hr.user@example.com", "domain": "example.com", "groups": ["hr"]},
    }

    latencies: list[float] = []
    started = time.perf_counter()
    with futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        work = [executor.submit(one_call, args.url, payload) for _ in range(args.requests)]
        for item in futures.as_completed(work):
            latencies.append(item.result())
    elapsed = time.perf_counter() - started

    throughput = len(latencies) / elapsed if elapsed else 0.0
    print(f"total_requests={len(latencies)}")
    print(f"elapsed_seconds={elapsed:.3f}")
    print(f"throughput_rps={throughput:.3f}")
    print(f"p50_latency_s={percentile(latencies, 50):.3f}")
    print(f"p95_latency_s={percentile(latencies, 95):.3f}")
    print(f"avg_latency_s={statistics.mean(latencies):.3f}")


if __name__ == "__main__":
    main()
