#!/usr/bin/env python3
"""Send the labeled test tickets to the real intake webhook, paced, and log each response.

Config comes from the environment (nothing secret is stored in this repo):
  SUPPORT_INTAKE_URL      e.g. https://<your-n8n>/webhook/support-intake
  SUPPORT_INTAKE_SECRET   value of the X-Intake-Secret header

Usage:
  python3 -I run_tests.py --phase A --delay 3
  python3 -I run_tests.py --phase B --delay 15 --limit 5     # small vision/geocoder batches
  python3 -I run_tests.py --only S007,S012                   # re-send specific tickets

Never retries: a retry would create a second ticket and skew the numbers.
Already-sent ids in results.jsonl are skipped, so an interrupted run resumes safely.
Photo tickets are skipped until photos.json maps their photo key to a public URL.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
RESULTS = HERE / "results.jsonl"


def post(url, secret, payload, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Intake-Secret": secret,
                                          "User-Agent": "support-test-runner/1.0"})  # default urllib UA is blocked by Cloudflare (1010)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code, body = r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read().decode()
    except Exception as e:  # network error or timeout
        return {"http": None, "error": str(e), "latency_s": round(time.monotonic() - t0, 2)}
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = {"raw": body[:200]}
    return {"http": code, "response": parsed, "latency_s": round(time.monotonic() - t0, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=["A", "B"])
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between tickets")
    ap.add_argument("--limit", type=int, help="send at most N tickets this run")
    ap.add_argument("--only", help="comma-separated ticket ids")
    args = ap.parse_args()

    url, secret = os.environ.get("SUPPORT_INTAKE_URL"), os.environ.get("SUPPORT_INTAKE_SECRET")
    if not url or not secret:
        sys.exit("Set SUPPORT_INTAKE_URL and SUPPORT_INTAKE_SECRET first.")

    data = json.load(open(HERE / "tickets.json"))
    tickets = data["phase_a"] + data["phase_b"]
    if args.phase:
        tickets = [t for t in tickets if t["phase"] == args.phase]
    if args.only:
        wanted = set(args.only.split(","))
        tickets = [t for t in tickets if t["id"] in wanted]

    photos = json.load(open(HERE / "photos.json")) if (HERE / "photos.json").exists() else {}
    done = set()
    if RESULTS.exists() and not args.only:
        done = {json.loads(l)["id"] for l in RESULTS.read_text().splitlines() if l.strip()}

    sent = 0
    for t in tickets:
        if t["id"] in done:
            continue
        if t["photo"] and t["photo"] not in photos:
            print(f"skip {t['id']}: no URL for photo '{t['photo']}' in photos.json")
            continue
        if args.limit and sent >= args.limit:
            break
        payload = {"channel": t["channel"], "customer_email": t["customer_email"],
                   "order_number": t["order_number"], "subject": t["subject"], "message": t["message"]}
        if t["photo"]:
            payload["attachments"] = [photos[t["photo"]]]
        res = post(url, "wrong-secret" if t.get("bad_secret") else secret, payload)
        row = {"id": t["id"], "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **res}
        with open(RESULTS, "a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        sent += 1
        r = res.get("response", {})
        print(f"{t['id']} http={res.get('http')} {res['latency_s']}s "
              f"intent={r.get('intent') if isinstance(r, dict) else None} ticket={r.get('ticket_number') if isinstance(r, dict) else None}")
        time.sleep(args.delay)
    print(f"sent {sent} this run")


if __name__ == "__main__":
    main()
