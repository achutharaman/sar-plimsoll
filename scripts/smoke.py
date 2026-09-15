"""Pre-demo smoke check against a deployment (stdlib only). Exits non-zero on any failure.

  python scripts/smoke.py --api https://<project>.web.app --email ... --password ... --api-key ...

Checks: health → config → sign-in → submit a file → identical resubmission returns the same score
from cache at $0 → history, stats and insights respond.
"""

import argparse
import sys
import time
import uuid

from seed import DEMO, Client, firebase_token, wait


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")
    if not ok:
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--dev-user")
    ap.add_argument("--email")
    ap.add_argument("--password")
    ap.add_argument("--api-key")
    args = ap.parse_args()

    anon = Client(args.api, "")
    started = time.monotonic()
    status, _ = anon.call("GET", "/health")
    check("health", status == 200, f"{time.monotonic() - started:.2f}s")
    status, config = anon.call("GET", "/v1/config")
    check(
        "config",
        status == 200 and config.get("rubric_version") is not None,
        config.get("auth_mode", ""),
    )

    token = (
        f"dev:{args.dev_user}"
        if args.dev_user
        else firebase_token(args.api_key, args.email, args.password)
    )
    client = Client(args.api, token)
    status, me = client.call("GET", "/v1/me")
    check("sign-in", status == 200, f"admin={me.get('admin')}")

    # A unique comment makes the first submission a guaranteed cache miss.
    content = (DEMO / "files" / "orders_v2.py").read_text() + f"\n# smoke {uuid.uuid4().hex}\n"
    status, first = client.json(
        "POST", "/v1/reviews", {"filename": "smoke_orders.py", "content": content}
    )
    check("submit accepted", status == 202, f"HTTP {status}")
    first = wait(
        client,
        f"/v1/reviews/{first['id']}",
        lambda r: r["status"] in ("done", "failed"),
        "smoke review",
    )
    check(
        "review completed",
        first["status"] == "done",
        f"score {first.get('score')} in {first['cost']['wall_ms'] / 1000:.1f}s, ${first['cost']['cost_usd']:.4f}",
    )

    started = time.monotonic()
    status, second = client.json(
        "POST", "/v1/reviews", {"filename": "smoke_orders.py", "content": content}
    )
    check(
        "resubmission served from cache",
        status == 200 and second["cost"]["cache_hit"],
        f"{time.monotonic() - started:.2f}s",
    )
    check(
        "identical score",
        second["result"]["score"] == first["result"]["score"],
        str(second["score"]),
    )
    check("zero cost", second["cost"]["cost_usd"] == 0)

    for path in ("/v1/history", "/v1/stats", "/v1/insights"):
        status, _ = client.call("GET", path)
        check(path, status == 200)
    print("all checks passed")


if __name__ == "__main__":
    main()
