"""Seed a deployment with demo data through the public API (stdlib only).

  python scripts/seed.py --api https://<api-or-hosting-url> --email demo@example.com --password ... --api-key <firebase web key>
  python scripts/seed.py --api http://localhost:8080 --dev-user demo:admin

Steps: ingest demo/rules.csv as a job (admin) → review orders.py v1 → v2 → v3 (a growth story on
one filename) → resubmit v3 (cache hit) → review checkout.ts, cache.go, UserService.java.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo"


class Client:
    def __init__(self, api: str, token: str):
        self.api = api.rstrip("/")
        self.token = token

    def call(
        self, method: str, path: str, body: bytes | None = None, content_type: str | None = None
    ):
        req = urllib.request.Request(self.api + path, data=body, method=method)
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        if content_type:
            req.add_header("Content-Type", content_type)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"null")

    def json(self, method: str, path: str, payload: dict):
        return self.call(method, path, json.dumps(payload).encode(), "application/json")

    def upload(self, path: str, filename: str, data: bytes):
        boundary = uuid.uuid4().hex
        body = (
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                f"Content-Type: text/csv\r\n\r\n"
            ).encode()
            + data
            + f"\r\n--{boundary}--\r\n".encode()
        )
        return self.call("POST", path, body, f"multipart/form-data; boundary={boundary}")


def firebase_token(api_key: str, email: str, password: str) -> str:
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
    payload = json.dumps({"email": email, "password": password, "returnSecureToken": True}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["idToken"]


def wait(client: Client, path: str, done, label: str, every: float = 3.0, limit: float = 900):
    started = time.monotonic()
    while True:
        status, body = client.call("GET", path)
        if status != 200:
            sys.exit(f"{label}: HTTP {status} {body}")
        if done(body):
            return body
        if time.monotonic() - started > limit:
            sys.exit(f"{label}: timed out")
        time.sleep(every)


def review(client: Client, filename: str, source: Path) -> dict:
    status, body = client.json(
        "POST", "/v1/reviews", {"filename": filename, "content": source.read_text()}
    )
    if status not in (200, 202):
        sys.exit(f"review {filename}: HTTP {status} {body}")
    if status == 202:
        body = wait(
            client,
            f"/v1/reviews/{body['id']}",
            lambda r: r["status"] in ("done", "failed") or r.get("stalled"),
            filename,
        )
    cost = body.get("cost") or {}
    print(
        f"  {filename:18} {body['status']:6} score {body.get('score')!s:>4}  "
        f"{'cache hit' if cost.get('cache_hit') else ('escalated' if cost.get('escalated') else 'flash only'):10} "
        f"${cost.get('cost_usd', 0):.4f}  {cost.get('wall_ms', 0) / 1000:.1f}s"
    )
    return body


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--api", required=True)
    ap.add_argument("--dev-user")
    ap.add_argument("--email")
    ap.add_argument("--password")
    ap.add_argument("--api-key")
    ap.add_argument("--skip-rules", action="store_true")
    args = ap.parse_args()

    if args.dev_user:
        token = f"dev:{args.dev_user}"
    elif args.email and args.password and args.api_key:
        token = firebase_token(args.api_key, args.email, args.password)
    else:
        sys.exit("pass --dev-user, or --email/--password/--api-key")
    client = Client(args.api, token)

    if not args.skip_rules:
        print("ingesting demo/rules.csv …")
        status, job = client.upload(
            "/admin/rules:ingest", "rules.csv", (DEMO / "rules.csv").read_bytes()
        )
        if status != 202:
            sys.exit(f"ingest: HTTP {status} {job}")
        job = wait(
            client,
            f"/admin/rules/ingest-jobs/{job['id']}",
            lambda j: j["status"] in ("done", "failed"),
            "ingest",
        )
        report = job.get("report") or {}
        print(
            f"  {job['status']}: {report.get('accepted')} accepted, {report.get('rejected_count')} rejected, corpus {report.get('corpus_version')}"
        )

    print("reviews …")
    for version in ("v1", "v2", "v3"):
        review(client, "orders.py", DEMO / "files" / f"orders_{version}.py")
    review(client, "orders.py", DEMO / "files" / "orders_v3.py")  # identical resubmission → cache
    for name in ("checkout.ts", "cache.go", "UserService.java"):
        review(client, name, DEMO / "files" / name)


if __name__ == "__main__":
    main()
