# Test harness: 100 labeled support tickets

A repeatable test of the intake webhook and the workflows behind it. Every ticket carries an expected intent, final ticket status and action, so the score is computed, not eyeballed.

## What is in the set

| Group | Count | Expected outcome |
|---|---|---|
| Order status (known order) | 22 | `auto_resolved`, reply prepared from the order record |
| Order status (unknown order) | 3 | Handed to a person |
| Address update, unfulfilled order | 10 | Geocoded, order updated, `auto_resolved` |
| Address update, shipped order | 3 | Carrier intercept, escalated |
| Address update, unverifiable address | 2 | `awaiting_customer`, order unchanged |
| Damage claim, clear damage, order up to $100 | 8 | Replacement order, `auto_resolved` |
| Damage claim, high-value order | 2 | Escalated, never auto-approved |
| Damage claim, no photo | 2 | Escalated, no model call |
| Damage claim, intact item in photo | 2 | Escalated |
| Refund request | 12 | Slack approval card, no payout |
| General inquiry | 20 | Handed to a person |
| Fraud-listed sender | 5 | Dropped, no ticket |
| Wrong secret | 2 | `401` |
| Quote and SQL-style characters | 3 | Handled like a normal ticket |
| Non-English (Spanish) | 2 | Handled like a normal ticket |
| Same message sent twice | 2 | Two tickets (no deduplication is claimed) |

Damage photos are 12 free-licence images from Wikimedia Commons (see `photo_credits.json`; files are linked, not stored here). They show broken dishware and glass, not the store's own products.

## Files

| File | Purpose |
|---|---|
| `build_testset.py` | Generates `tickets.json` and `seed.sql` deterministically |
| `seed.sql` | Test customers, orders and fraud-list entries, all tagged `@test.example` / `LUM-9xxxx` |
| `cleanup.sql` | Removes everything the test created |
| `run_tests.py` | Sends tickets to the real webhook, paced, never retries |
| `score.py` | Joins webhook responses with an export of the ticket ledger and prints the summary |
| `photos.json`, `photo_credits.json` | Photo URLs and their licences |

## Run it

```bash
python3 build_testset.py                 # writes tickets.json and seed.sql
# run seed.sql on your Supabase project
export SUPPORT_INTAKE_URL=https://<your-n8n>/webhook/support-intake
export SUPPORT_INTAKE_SECRET=<your intake secret>
python3 run_tests.py --phase A --delay 3     # tickets with no photo or geocoder call
python3 run_tests.py --phase B --delay 60    # address and photo tickets, paced for the geocoder and vision-model limits
# export support_tickets for the test customers to db_export.json, then:
python3 score.py
# run cleanup.sql when done
```

The workflow sends no email: replies are only written to the ticket ledger, so a run does not touch any inbox.

## Measured results (one run, 100 tickets sent)

| Metric | Result |
|---|---|
| Intent classified correctly | 93 of 93 tickets |
| Final status and action match the label | 90 of 93 (97%) |
| Damage-photo outcome correct | 11 of 12 |
| Auto-resolved when a human was expected | 0 |
| Fraud-listed senders dropped with no ticket | 5 of 5 |
| Wrong secret rejected with 401 | 2 of 2 |
| Quote and SQL-style inputs handled | 3 of 3 |
| Acknowledgement time (classify and log ticket) | median 25.9 s, 90th percentile 29.6 s |
| Ticket creation to resolution, auto-resolved runs | median 0 s, 90th percentile 6 s, max 7 s (the longer ones are vision replacements) |

For this run the intent model was a 7B model self-hosted on a CPU-only VM, and the vision model was a hosted free-tier model. The exported workflows in this repo keep the defaults described in the main README; swapping the models is a one-node change.

### Limits
- Three tickets ended differently from their label: two address tickets reached the geocoder's 10 s timeout, which the workflow treats as unverified (the customer is asked to confirm), and one factory photo of broken plates was escalated to a person instead of replaced.
- Acknowledgement time reflects a 7B model on a CPU-only VM; a hosted model or a GPU shortens it.
- Synthetic, templated messages: 100% intent accuracy here is a ceiling, not a production figure.
- 12 photos, one reviewer's ground truth, a single run.
- Slack approval outcomes and the reconciliation rows were not measured.
- Replies are prepared and logged, not sent, so delivery is not tested.
