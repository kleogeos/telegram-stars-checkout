#!/usr/bin/env python3
"""Parsing + rendering for the paid custom-invoice flow inside bot.py.

New product, same bot/token (no new Telegram app, no new human access needed):
a buyer sends a plain-text /invoice message with their line items, gets a
priced preview + a Stars pay link, and on payment receives a polished HTML
invoice as a document. Targets the non-technical buyer who would rather type
a Telegram message than run a CLI or edit JSON by hand — the free
freelance-invoice-cli already does this for anyone comfortable with a
terminal; this is the same value delivered to people who aren't.

render_invoice() intentionally mirrors render() in
kleogeos/freelance-invoice-cli/invoice.py byte-for-byte (same output format,
so a buyer who later wants to self-serve isn't surprised by a different
look). Vendored rather than imported across repos: a runtime fetch from
another repo would add a network dependency and the raw-CDN caching lag
already documented elsewhere in this project. Keep in sync by hand if
invoice.py's render() changes.
"""
import html


def money(n):
    return f"${n:,.2f}"


def render_invoice(data):
    items = data["items"]
    subtotal = sum(i["qty"] * i["rate"] for i in items)
    tax_rate = data.get("tax_rate", 0)
    tax = subtotal * tax_rate / 100
    total = subtotal + tax

    rows = "\n".join(
        f"<tr><td>{html.escape(i['description'])}</td>"
        f"<td style='text-align:right'>{html.escape(str(i['qty']))}</td>"
        f"<td style='text-align:right'>{money(i['rate'])}</td>"
        f"<td style='text-align:right'>{money(i['qty'] * i['rate'])}</td></tr>"
        for i in items
    )

    notes = html.escape(data.get("notes", ""))

    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Invoice {html.escape(data.get('invoice_number', ''))}</title>
<style>
body{{font-family:-apple-system,Arial,sans-serif;max-width:680px;margin:40px auto;color:#1a1a1a}}
table{{width:100%;border-collapse:collapse;margin-top:24px}}
th,td{{padding:8px;border-bottom:1px solid #ddd}}
th{{text-align:left;color:#666;font-size:13px;text-transform:uppercase}}
.total{{font-size:20px;font-weight:bold}}
.meta{{color:#666;font-size:14px}}
@media print{{body{{margin:0}}}}
</style></head><body>
<h1>Invoice {html.escape(data.get('invoice_number', ''))}</h1>
<p class="meta">From: {html.escape(data.get('from', ''))}<br>
To: {html.escape(data.get('to', ''))}<br>
Date: {html.escape(data.get('date', ''))} &middot; Due: {html.escape(data.get('due_date', ''))}</p>
<table>
<tr><th>Description</th><th style="text-align:right">Qty</th><th style="text-align:right">Rate</th><th style="text-align:right">Amount</th></tr>
{rows}
</table>
<p style="text-align:right;margin-top:16px">Subtotal: {money(subtotal)}<br>
{"Tax (" + str(tax_rate) + "%): " + money(tax) + "<br>" if tax_rate else ""}
<span class="total">Total: {money(total)}</span></p>
{"<p class='meta'>" + notes + "</p>" if notes else ""}
</body></html>"""


USAGE = """To get a custom invoice, send one message in this format:

/invoice
from: Your Name
to: Client Name
invoice_number: INV-001
date: 2026-10-20
due_date: 2026-11-20
tax_rate: 0
notes: Thanks for your business
item: Website redesign | 10 | 50
item: Logo design | 1 | 200

- "item" lines: description | quantity | rate (one per line, at least one required)
- from/to are required. invoice_number/date/due_date/tax_rate/notes are optional.
- You'll get a priced preview and a payment link first — the polished HTML file
  is only generated and sent after payment (100 Stars, about $1.3)."""


def parse_invoice_text(text):
    """Parses the /invoice message body into the dict render_invoice() expects.
    Returns (data, None) on success or (None, error_message) on failure —
    never raises, since this runs directly on untrusted user input."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if lines and lines[0].strip().lower() in ("/invoice", "/invoice@"):
        lines = lines[1:]

    data = {"items": []}
    for ln in lines:
        if ":" not in ln:
            return None, f"Couldn't parse this line (missing ':'): {ln!r}\n\n{USAGE}"
        key, _, value = ln.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "item":
            parts = [p.strip() for p in value.split("|")]
            if len(parts) != 3:
                return None, (
                    f"Item line needs exactly 3 parts separated by '|' "
                    f"(description | qty | rate), got: {ln!r}\n\n{USAGE}"
                )
            desc, qty_s, rate_s = parts
            try:
                qty = float(qty_s)
                rate = float(rate_s)
            except ValueError:
                return None, f"qty and rate must be numbers, got: {ln!r}\n\n{USAGE}"
            if not desc:
                return None, f"Item description can't be empty: {ln!r}\n\n{USAGE}"
            data["items"].append({"description": desc, "qty": qty, "rate": rate})
        elif key == "tax_rate":
            try:
                data["tax_rate"] = float(value)
            except ValueError:
                return None, f"tax_rate must be a number, got: {value!r}\n\n{USAGE}"
        elif key in ("from", "to", "invoice_number", "date", "due_date", "notes"):
            data[key] = value
        # unknown keys are silently ignored rather than rejected — keeps the
        # format forgiving of e.g. a stray colon in a copy-pasted notes line
        # elsewhere (notes is matched above before this branch could trigger).

    if not data["items"]:
        return None, f"Need at least one 'item:' line.\n\n{USAGE}"
    if not data.get("from") or not data.get("to"):
        return None, f"Both 'from:' and 'to:' are required.\n\n{USAGE}"
    return data, None


def preview_text(data):
    subtotal = sum(i["qty"] * i["rate"] for i in data["items"])
    tax_rate = data.get("tax_rate", 0)
    total = subtotal + subtotal * tax_rate / 100
    lines = [f"Invoice preview — {data['from']} → {data['to']}"]
    for i in data["items"]:
        lines.append(f"  {i['description']}: {i['qty']} x {money(i['rate'])} = {money(i['qty']*i['rate'])}")
    lines.append(f"Total: {money(total)}")
    return "\n".join(lines)
