#!/usr/bin/env python3
"""Score a run: join results.jsonl (webhook responses) with db_export.json (ticket ledger rows).

db_export.json: JSON list of rows from support_tickets for the test customers:
  select ticket_number, customer_email, intent, status, action_executed, resolution_time_seconds
  from support_tickets where customer_email like '%@test.example';

Usage: python3 -I score.py   -> prints a Markdown summary and writes summary.json
"""
import collections
import json
import statistics
from pathlib import Path

HERE = Path(__file__).parent


def pct(n, d):
    return f"{n}/{d} ({100 * n / d:.0f}%)" if d else "n/a"


def p90(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(0.9 * len(xs)))] if xs else None


def main():
    data = json.load(open(HERE / "tickets.json"))
    labels = {t["id"]: t for t in data["phase_a"] + data["phase_b"]}
    results = {}
    for line in (HERE / "results.jsonl").read_text().splitlines():
        r = json.loads(line)
        results[r["id"]] = r
    ledger = {row["ticket_number"]: row for row in json.load(open(HERE / "db_export.json"))}

    rows = []
    for tid, r in results.items():
        t = labels[tid]
        resp = r.get("response") or {}
        tn = resp.get("ticket_number") if isinstance(resp, dict) else None
        db = ledger.get(tn, {})
        if not tn:  # client timed out before the ack: fall back to the ticket for this email, if unique
            same = [row for row in ledger.values() if row["customer_email"] == t["customer_email"]]
            if len(same) == 1:
                db, tn = same[0], same[0]["ticket_number"]
                resp = {"ticket_number": tn, "intent": db.get("intent"), "timed_out_client": True}
        rows.append({"id": tid, "label": t, "http": r.get("http"), "latency": r["latency_s"],
                     "ack_intent": resp.get("intent") if isinstance(resp, dict) else None,
                     "resp_status": resp.get("status") if isinstance(resp, dict) else None,
                     "ticket": tn, "db": db})

    out = {"tickets_sent": len(rows), "not_sent": sorted(set(labels) - set(results))}
    ticketed = [x for x in rows if x["label"]["expect_status"] not in ("no_ticket",)]

    # Intent accuracy (all tickets that should create a ticket)
    ok = [x for x in ticketed if x["ack_intent"] == x["label"]["expect_intent"]]
    out["intent_accuracy"] = pct(len(ok), len(ticketed))
    by_intent = collections.defaultdict(lambda: [0, 0])
    for x in ticketed:
        by_intent[x["label"]["expect_intent"]][1] += 1
        by_intent[x["label"]["expect_intent"]][0] += x["ack_intent"] == x["label"]["expect_intent"]
    out["intent_by_class"] = {k: pct(*v) for k, v in sorted(by_intent.items())}
    out["intent_misses"] = [f"{x['id']}: expected {x['label']['expect_intent']}, got {x['ack_intent']}"
                            for x in ticketed if x["ack_intent"] != x["label"]["expect_intent"]]

    # Outcome (final ledger status + action) vs label
    def outcome_ok(x):
        return x["db"].get("status") == x["label"]["expect_status"] and \
               x["db"].get("action_executed") == x["label"]["expect_action"]
    good = [x for x in ticketed if outcome_ok(x)]
    out["outcome_correct"] = pct(len(good), len(ticketed))
    out["outcome_misses"] = [f"{x['id']}: expected {x['label']['expect_status']}/{x['label']['expect_action']}, "
                             f"got {x['db'].get('status')}/{x['db'].get('action_executed')}"
                             for x in ticketed if not outcome_ok(x)]

    # Escalation direction: should a human have seen it?
    def human(s):
        return s in ("escalated_to_human", "awaiting_customer")
    gate_ok = [x for x in ticketed if human(x["db"].get("status")) == human(x["label"]["expect_status"])]
    out["human_vs_auto_correct"] = pct(len(gate_ok), len(ticketed))
    auto_wrong = [x for x in ticketed if x["db"].get("status") == "auto_resolved"
                  and x["label"]["expect_status"] != "auto_resolved"]
    out["auto_resolved_when_human_expected"] = len(auto_wrong)

    # Edge cases
    fraud = [x for x in rows if x["label"]["expect_action"] == "dropped_fraud"]
    out["fraud_dropped_no_ticket"] = pct(sum(1 for x in fraud if x["http"] == 200 and x["resp_status"] == "flagged" and not x["ticket"]), len(fraud))
    bad = [x for x in rows if x["label"]["expect_action"] == "rejected_401"]
    out["wrong_secret_rejected_401"] = pct(sum(1 for x in bad if x["http"] == 401), len(bad))
    hostile = [x for x in rows if x["label"].get("tag") == "hostile"]
    out["hostile_inputs_handled"] = pct(sum(1 for x in hostile if x["http"] == 200 and outcome_ok(x)), len(hostile))
    dups = [x for x in rows if x["label"].get("tag") == "duplicate"]
    out["duplicate_pair_tickets_created"] = len({x["ticket"] for x in dups if x["ticket"]})

    # Damage fast path against ground truth photo labels
    dmg = [x for x in ticketed if x["label"]["photo"]]
    out["vision_fast_path_correct"] = pct(sum(outcome_ok(x) for x in dmg), len(dmg))

    # Timing
    lat = [x["latency"] for x in ticketed if x["http"] == 200]
    if lat:
        out["intake_ack_seconds"] = {"median": round(statistics.median(lat), 1), "p90": round(p90(lat), 1), "max": max(lat)}
    res_t = [x["db"]["resolution_time_seconds"] for x in good if x["db"].get("resolution_time_seconds") is not None]
    if res_t:
        out["auto_resolve_seconds"] = {"n": len(res_t), "median": statistics.median(res_t), "p90": p90(res_t), "max": max(res_t)}
    out["http_errors"] = [f"{x['id']}: http={x['http']}" for x in rows
                          if x["http"] not in (200, 401)]

    json.dump(out, open(HERE / "summary.json", "w"), indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
