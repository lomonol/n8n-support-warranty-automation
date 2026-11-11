#!/usr/bin/env python3
"""Build the 100-ticket labeled test set and the Supabase seed for it.

Outputs (next to this file):
  tickets.json   100 tickets with expected intent, final status and action
  seed.sql       idempotent seed: test customers, orders, fraud-list entries
  cleanup.sql    removes everything the test creates (tagged by @test.example / LUM-9xxxx)

Deterministic: same seed, same set. No secrets in any output.
"""
import json
import random
from pathlib import Path

HERE = Path(__file__).parent
rnd = random.Random(2026)
DOMAIN = "test.example"

# ---------------------------------------------------------------- seed model
customers, orders = [], []
_order_n = 90000


def new_order(status, amount, city="Chicago", state="IL", zipc="60601", tracking=None,
              item=("LUM-CERAMIC-MUG", "Handmade Artisan Mug Set")):
    """Create one customer + one order and return the order dict."""
    global _order_n
    _order_n += 1
    n = _order_n - 90000
    email = f"t{n:02d}@{DOMAIN}"
    order = {
        "order_number": f"LUM-{_order_n}",
        "email": email,
        "status": status,
        "amount": amount,
        "address": {"apt": "", "zip": zipc, "city": city, "state": state,
                    "street": f"{100 + n} Main Street", "country": "USA"},
        "tracking": tracking or (f"FDX-TEST{_order_n}" if status != "unfulfilled" else None),
        "item": {"sku": item[0], "name": item[1], "price": amount, "qty": 1},
    }
    customers.append({"email": email, "first": f"Test{n:02d}", "tier": "standard",
                      "spend": amount, "count": 1})
    orders.append(order)
    return order


CITIES = [("Chicago", "IL", "60601"), ("Portland", "OR", "97201"), ("Austin", "TX", "78701"),
          ("Denver", "CO", "80202"), ("Seattle", "WA", "98101"), ("Boston", "MA", "02108"),
          ("Phoenix", "AZ", "85004"), ("Atlanta", "GA", "30303")]
ITEMS = [("LUM-CERAMIC-MUG", "Handmade Artisan Mug Set"), ("LUM-DIFFUSER-OAK", "Nordic Essential Oil Diffuser"),
         ("LUM-VASE-STONE", "Stoneware Bud Vase"), ("LUM-PLATE-SET", "Glazed Dinner Plate Set"),
         ("LUM-CANDLE-3", "Soy Candle Trio")]


def city():
    return rnd.choice(CITIES)


def order(status, amount, **kw):
    c = kw.pop("city_tuple", None) or city()
    return new_order(status, amount, c[0], c[1], c[2], item=rnd.choice(ITEMS), **kw)


# ---------------------------------------------------------------- message banks
STATUS_MSG = [
    "Hi, where is my order {o}? It has been a while.",
    "Can you tell me the delivery status of {o}?",
    "Any update on {o}? I need to know when it arrives.",
    "Tracking for {o} has not moved. What is going on?",
    "Just checking in on {o}. When will it be delivered?",
    "Order {o}: has it shipped yet?",
    "I ordered last week ({o}) and have not heard anything. Status please.",
]
STATUS_NO_ORDER = [
    "Where is my package? I cannot find my confirmation email and do not have the number.",
    "Order {o} is not showing up in my account. Can you check it?",
    "I placed an order a few days ago and nothing has arrived. Order number is {o}.",
]
ADDR_MSG = [
    "I made a typo on {o}. Please change my apartment to {c}.",
    "Quick fix for {o}: the unit should be {c}, please update before it ships.",
    "Hello, for order {o} can you add {c} to my delivery address?",
    "Please correct my address on {o} to include {c}.",
    "Order {o}: I forgot my unit number. It is {c}.",
]
CORRECTIONS = ["Apt 4B", "Apt 12", "Unit 3C", "Suite 200", "Apt 7A", "Floor 2", "Apt 9", "Unit 15"]
DAMAGE_MSG = [
    "My order {o} arrived broken. Photo attached.",
    "The item from {o} is cracked. I have attached a picture.",
    "Received {o} today and it is damaged, see the photo.",
    "{o} came smashed in the box. Please see the attached image.",
    "Damaged on arrival ({o}). Photo of the problem attached.",
]
DAMAGE_NO_PHOTO = [
    "My order {o} arrived broken but I did not take a photo. Can you help?",
    "Item from {o} is cracked. I will not be able to send a picture.",
]
REFUND_MSG = [
    "I want my money back for {o}. This is not what I expected.",
    "Please refund order {o}. I am really disappointed.",
    "Cancel {o} and refund me, the quality is poor.",
    "I demand a full refund on {o} right now.",
    "Not happy with {o}. Refund it please.",
]
GENERAL_MSG = [
    "Is the Stoneware Bud Vase dishwasher safe?",
    "Do you ship to Canada?",
    "What is your return policy for opened items?",
    "Can I get the diffuser in a different color?",
    "How should I clean the glazed plates?",
    "Do you offer gift wrapping?",
    "Are your candles made with natural wax?",
    "When do you restock the mug set?",
    "Do you have a store I can visit in person?",
    "Is there a discount for buying in bulk?",
    "What oils work best in the Nordic diffuser?",
    "How long does standard shipping usually take?",
    "Can I change the email on my account?",
    "Do you sell gift cards?",
    "Is the oak diffuser safe around pets?",
    "What is the warranty on your ceramics?",
    "Can I pick up an order instead of shipping?",
    "Do you have a wholesale program?",
    "Which payment methods do you accept?",
    "Are the candles safe for small bedrooms?",
]
HOSTILE_MSG = [
    "Status of {o}? Robert'); DROP TABLE support_tickets;-- thanks",
    "Where is {o}?? \"quoted\" 'single' `tick` {{braces}} <b>bold</b> \\backslash",
    "Check {o} please. ' OR '1'='1' -- also éèü ☃ emoji \U0001F4E6",
]
SPANISH_MSG = [
    "Hola, ¿dónde está mi pedido {o}? Todavía no ha llegado.",
    "Buenos días, quisiera saber el estado de mi orden {o}, por favor.",
]

tickets = []


def add(**t):
    t["id"] = f"S{len(tickets) + 1:03d}"
    t.setdefault("channel", rnd.choice(["email", "email", "chat", "web_form"]))
    t.setdefault("photo", None)
    t.setdefault("expect_http", 200)
    tickets.append(t)


def route(order_row, msg, intent, status, action, phase="A", **extra):
    add(customer_email=order_row["email"], order_number=order_row["order_number"],
        subject=f"Re: {order_row['order_number']}", message=msg,
        expect_intent=intent, expect_status=status, expect_action=action, phase=phase, **extra)


# 1) ORDER_STATUS: 22 with a real order, 3 with an unknown order -> human
for i in range(22):
    st = ["unfulfilled", "shipped", "delivered"][i % 3]
    o = order(st, rnd.choice([34, 48, 62, 78, 95]))
    route(o, STATUS_MSG[i % len(STATUS_MSG)].format(o=o["order_number"]),
          "ORDER_STATUS", "auto_resolved", "order_status_reply_prepared")
for i in range(3):
    fake = f"LUM-99{i + 1:03d}"
    c = order("delivered", 40)  # owns a real customer record, but the cited order does not exist
    add(customer_email=c["email"], order_number=fake, subject="Order not found",
        message=STATUS_NO_ORDER[i].format(o=fake), expect_intent="ORDER_STATUS",
        expect_status="escalated_to_human", expect_action="human_handoff", phase="A")

# 2) ADDRESS_UPDATE: 10 unfulfilled, 3 shipped, 2 geocode failures
for i in range(10):
    o = order("unfulfilled", rnd.choice([42, 55, 68, 85, 92]))
    route(o, ADDR_MSG[i % len(ADDR_MSG)].format(o=o["order_number"], c=CORRECTIONS[i % len(CORRECTIONS)]),
          "ADDRESS_UPDATE", "auto_resolved", "address_updated_pre_fulfillment", phase="B")
for i in range(3):
    o = order("shipped", 70)
    route(o, ADDR_MSG[i].format(o=o["order_number"], c=CORRECTIONS[i]),
          "ADDRESS_UPDATE", "escalated_to_human", "carrier_intercept_requested", phase="B")
for i in range(2):
    o = new_order("unfulfilled", 60, "Zzyzxville", "ZZ", "00000", item=ITEMS[0])
    route(o, ADDR_MSG[i + 3].format(o=o["order_number"], c=CORRECTIONS[i + 3]),
          "ADDRESS_UPDATE", "awaiting_customer", "address_unverified_customer_asked", phase="B")

# 3) DAMAGED_ITEM: 8 clear <= $100, 2 high value, 2 no photo, 2 intact photos
for i in range(8):
    o = order("delivered", rnd.choice([38, 52, 64, 79, 88]))
    route(o, DAMAGE_MSG[i % len(DAMAGE_MSG)].format(o=o["order_number"]), "DAMAGED_ITEM",
          "auto_resolved", "replacement_order_created", phase="B",
          photo=f"damaged_{i + 1:02d}", photo_truth="damage")
for i in range(2):
    o = order("delivered", rnd.choice([240, 310]))
    route(o, DAMAGE_MSG[i].format(o=o["order_number"]), "DAMAGED_ITEM", "escalated_to_human",
          "escalated_for_human_review", phase="B", photo=f"damaged_hv_{i + 1:02d}", photo_truth="damage")
for i in range(2):
    o = order("delivered", 58)
    route(o, DAMAGE_NO_PHOTO[i].format(o=o["order_number"]), "DAMAGED_ITEM", "escalated_to_human",
          "escalated_for_human_review", phase="A")
for i in range(2):
    o = order("delivered", 66)
    route(o, DAMAGE_MSG[i + 2].format(o=o["order_number"]), "DAMAGED_ITEM", "escalated_to_human",
          "escalated_for_human_review", phase="B", photo=f"intact_{i + 1:02d}", photo_truth="no_damage")

# 4) REFUND_REQUEST: 12, all go to the Slack approval card
for i in range(12):
    o = order("delivered", rnd.choice([45, 72, 99, 140, 260, 480]))
    route(o, REFUND_MSG[i % len(REFUND_MSG)].format(o=o["order_number"]), "REFUND_REQUEST",
          "escalated_to_human", "escalated_to_slack_hitl", phase="A")

# 5) GENERAL_INQUIRY: 20, all to a human (no order cited)
for i in range(20):
    c = order("delivered", 50)
    add(customer_email=c["email"], order_number="", subject="Question", message=GENERAL_MSG[i],
        expect_intent="GENERAL_INQUIRY", expect_status="escalated_to_human",
        expect_action="human_handoff", phase="A")

# 6) Edge cases (14): fraud 5, wrong secret 2, hostile 3, Spanish 2, duplicate 2
FRAUD = [f"fraud{i + 1}@{DOMAIN}" for i in range(5)]
for f in FRAUD:
    add(customer_email=f, order_number="", subject="Urgent", message="Refund me to a different card now.",
        expect_intent=None, expect_status="no_ticket", expect_action="dropped_fraud", phase="A",
        expect_response_status="flagged")
for i in range(2):
    o = order("shipped", 50)
    add(customer_email=o["email"], order_number=o["order_number"], subject="x",
        message="Where is my order?", expect_intent=None, expect_status="no_ticket",
        expect_action="rejected_401", expect_http=401, phase="A", bad_secret=True)
for i in range(3):
    o = order("shipped", 60)
    route(o, HOSTILE_MSG[i].format(o=o["order_number"]), "ORDER_STATUS", "auto_resolved",
          "order_status_reply_prepared", phase="A", tag="hostile")
for i in range(2):
    o = order("shipped", 74)
    route(o, SPANISH_MSG[i].format(o=o["order_number"]), "ORDER_STATUS", "auto_resolved",
          "order_status_reply_prepared", phase="A", tag="non_english")
dup = order("shipped", 80)
for _ in range(2):
    route(dup, f"Where is {dup['order_number']}? Sent twice by mistake.", "ORDER_STATUS", "auto_resolved",
          "order_status_reply_prepared", phase="A", tag="duplicate", expect_ticket_count_for_pair=2)

assert len(tickets) == 100, len(tickets)

# Shuffle within phase so intents interleave, keep ids stable (ids assigned before shuffle).
phase_a = [t for t in tickets if t["phase"] == "A"]
phase_b = [t for t in tickets if t["phase"] == "B"]
rnd.shuffle(phase_a)
rnd.shuffle(phase_b)
json.dump({"phase_a": phase_a, "phase_b": phase_b}, open(HERE / "tickets.json", "w"), indent=1, ensure_ascii=False)


# ---------------------------------------------------------------- SQL
def q(s):
    return "'" + str(s).replace("'", "''") + "'"


lines = ["-- Seed for the 100-ticket support test. Idempotent. Run on the ENT-03 Supabase project.",
         "-- All rows are tagged: emails @test.example, orders LUM-9xxxx. cleanup.sql removes them.", "",
         "delete from support_damage_claims where ticket_id in (select id from support_tickets where customer_email like '%@test.example');",
         "delete from support_tickets where customer_email like '%@test.example';",
         "delete from support_orders where customer_email like '%@test.example';",
         "delete from support_customers where email like '%@test.example';",
         "delete from support_fraud_blacklist where email_or_phone like '%@test.example';", ""]
item_idx = {i[0]: n for n, i in enumerate(ITEMS)}
lines.append("with o(n, amount, st, city, state, zip, item) as (values")
vals = []
for n, o in enumerate(orders, 1):
    a = o["address"]
    vals.append(f"  ({n}, {o['amount']}, {q(o['status'])}, {q(a['city'])}, {q(a['state'])}, {q(a['zip'])}, {item_idx[o['item']['sku']]})")
lines.append(",\n".join(vals) + "),")
lines.append("items(i, sku, name) as (values " + ", ".join(f"({n}, {q(i[0])}, {q(i[1])})" for n, i in enumerate(ITEMS)) + "),")
lines.append("c as (insert into support_customers (email, first_name, last_name, ltv_tier, total_spend, order_count)")
lines.append("  select 't' || lpad(n::text, 2, '0') || '@test.example', 'Test' || lpad(n::text, 2, '0'), 'Tester', 'standard', amount, 1 from o returning id, email)")
lines.append("insert into support_orders (order_number, customer_id, customer_email, total_amount, currency, fulfillment_status, shipping_address, tracking_number, carrier, items)")
lines.append("select 'LUM-' || (90000 + o.n), c.id, c.email, o.amount, 'USD', o.st,")
lines.append("  jsonb_build_object('apt', '', 'zip', o.zip, 'city', o.city, 'state', o.state, 'street', (100 + o.n) || ' Main Street', 'country', 'USA'),")
lines.append("  case when o.st = 'unfulfilled' then null else 'FDX-TEST' || (90000 + o.n) end, 'FedEx',")
lines.append("  jsonb_build_array(jsonb_build_object('sku', items.sku, 'name', items.name, 'price', o.amount, 'qty', 1))")
lines.append("from o join c on c.email = 't' || lpad(o.n::text, 2, '0') || '@test.example' join items on items.i = o.item;")
lines.append("")
lines.append("insert into support_fraud_blacklist (email_or_phone, reason) values")
lines.append(",\n".join(f"  ({q(f)}, 'test fraud list')" for f in FRAUD) + ";")
(HERE / "seed.sql").write_text("\n".join(lines) + "\n")

(HERE / "cleanup.sql").write_text("""-- Remove everything the support test created (tickets, claims, replacement orders, seeds).
delete from support_damage_claims where ticket_id in (select id from support_tickets where customer_email like '%@test.example');
delete from support_tickets where customer_email like '%@test.example';
delete from support_orders where customer_email like '%@test.example';
delete from support_customers where email like '%@test.example';
delete from support_fraud_blacklist where email_or_phone like '%@test.example';
-- Also delete Airtable Operations Hub rows whose Customer_Email ends in @test.example.
""")

print(f"{len(tickets)} tickets (A={len(phase_a)}, B={len(phase_b)}), {len(orders)} orders, {len(customers)} customers")
