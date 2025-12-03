# app.py
"""
Streamlit invoice maker
- 3 templates (Cream Minimalist, Playful Pastel, Modern Monochrome)
- Auto invoice numbering per person with logs in SQLite
- Save PDF files to ./invoices and show history & download links
- Cream theme UI touches
"""

import streamlit as st
from datetime import datetime, timedelta
import sqlite3
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from io import BytesIO
import base64
from supabase import create_client, Client
import os

# -----------------------
# Helpers & persistence
# -----------------------

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

ROMAN = {
    1: "I", 2: "II", 3: "III", 4: "IV", 5: "V", 6: "VI",
    7: "VII", 8: "VIII", 9: "IX", 10: "X", 11: "XI", 12: "XII"
}

def to_roman_month(dt: datetime):
    return ROMAN.get(dt.month, str(dt.month))

def make_initials(name: str):
    parts = [p for p in name.strip().split() if p]
    if len(parts) == 0:
        return "XX"
    if len(parts) == 1:
        s = parts[0]
        return (s[:2]).upper()
    return (parts[0][0] + parts[-1][0]).upper()

def get_next_sequence(initials: str, year: int):
    query = (
        supabase.table("invoices")
        .select("seq, invoice_date")
        .eq("initials", initials)
        .execute()
    )
    rows = query.data
    if not rows:
        return 1
    # Filter by same year
    filtered = [r for r in rows if str(year) in (r["invoice_date"] or "")]
    if not filtered:
        return 1
    last_seq = max(r["seq"] for r in filtered)
    return last_seq + 1

def build_invoice_number(initials: str, seq: int, dt: datetime):
    seq_s = f"{seq:03d}"
    roman = to_roman_month(dt)
    year = dt.year
    return f"{seq_s}/INV-{initials}/{roman}/{year}"

def save_invoice_record(
    name, initials, seq, invoice_no, invoice_date,
    due_date, template, total, pdf_path,
    bank, account_name, account_no, swift, currency_symbol
):
    data = {
        "name": name,
        "initials": initials,
        "seq": seq,
        "invoice_no": invoice_no,
        "invoice_date": invoice_date.isoformat(),
        "due_date": due_date.isoformat(),
        "template": template,
        "total": total,
        "pdf_path": pdf_path,
        "bank": bank,
        "account_name": account_name,
        "account_no": account_no,
        "swift": swift,
        "currency": currency_symbol,
    }
    supabase.table("invoices").insert(data).execute()

def fetch_history(limit=100):
    res = supabase.table("invoices").select("*").order("created_at", desc=True).limit(limit).execute()
    return res.data or []

def get_last_remittance(name: str):
    if not name:
        return {}
    res = (
        supabase.table("invoices")
        .select("bank, account_name, account_no, swift")
        .eq("name", name)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if res.data:
        row = res.data[0]
        return {
            "bank": row.get("bank", ""),
            "account_name": row.get("account_name", ""),
            "account_no": row.get("account_no", ""),
            "swift": row.get("swift", ""),
        }
    return {}

# -----------------------
# PO Helpers
# -----------------------

def get_next_po_sequence(initials: str, year: int):
    query = (
        supabase.table("purchase_orders")
        .select("seq, po_date")
        .eq("initials", initials)
        .execute()
    )
    rows = query.data
    if not rows:
        return 1
    filtered = [r for r in rows if str(year) in (r["po_date"] or "")]
    if not filtered:
        return 1
    return max(r["seq"] for r in filtered) + 1

def build_po_number(initials: str, seq: int, dt: datetime):
    seq_s = f"{seq:03d}"
    roman = to_roman_month(dt)
    year = dt.year
    return f"{seq_s}/PO-{initials}/{roman}/{year}"

def save_po_record(vendor_name, initials, seq, po_no, po_date,
                   template, total, pdf_url, issuer_name, issuer_address,
                   currency_symbol):
    data = {
        "vendor_name": vendor_name,
        "initials": initials,
        "seq": seq,
        "po_no": po_no,
        "po_date": po_date.isoformat(),
        "template": template,
        "total": total,
        "pdf_url": pdf_url,
        "issuer_name": issuer_name,
        "issuer_address": issuer_address,
        "currency": currency_symbol,
    }
    supabase.table("purchase_orders").insert(data).execute()

# -----------------------
# PDF generation using reportlab
# -----------------------

def draw_wrapped_string(cnv, text, x, y, max_width, leading=12):
    from reportlab.pdfbase.pdfmetrics import stringWidth
    lines = []
    words = text.split()
    line = ""
    for w in words:
        test = (line + " " + w).strip()
        if stringWidth(test, "Helvetica", 10) <= max_width:
            line = test
        else:
            lines.append(line)
            line = w
    if line:
        lines.append(line)
    for ln in lines:
        cnv.drawString(x, y, ln)
        y -= leading
    return y

def create_pdf_bytes(data: dict, template: str) -> bytes:
    """
    data: dict containing:
      - vendor_name, vendor_address
      - invoice_no, invoice_date, due_date
      - bill_to, bill_address
      - items, total
      - remittance
    template: one of 'cream', 'pastel', 'mono'
    """
    buffer = BytesIO()
    cnv = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    left, right = 20 * mm, 20 * mm
    top = height - 20 * mm
    usable_width = width - left - right

    # Colors per template
    if template == "cream":
        accent = colors.HexColor("#795757")      # muted rose-brown for lines
        text_color = colors.HexColor("#3B3030")  # deep brown text
        header_bg = colors.HexColor("#FFF0D1")   # warm cream header
    elif template == "pastel":
        accent = colors.HexColor("#D97D55")      # terracotta accent
        text_color = colors.HexColor("#6FA4AF")  # gentle teal-gray text
        header_bg = colors.HexColor("#F4E9D7")   # soft neutral header
    else:  # mono
        accent = colors.HexColor("#948979")      # soft taupe accent
        text_color = colors.HexColor("#222831")  # charcoal text
        header_bg = colors.HexColor("#DFD0B8")   # pale sand header

    # Header band
    cnv.setFillColor(header_bg)
    cnv.rect(left, top - 30 * mm, usable_width, 30 * mm, fill=True, stroke=False)

    cnv.setFillColor(text_color)
    cnv.setFont("Helvetica-Bold", 18)
    cnv.drawString(left + 6 * mm, top - 10 * mm, "INVOICE")

    cnv.setFont("Helvetica", 10)
    cnv.drawString(left + 6 * mm, top - 16 * mm, f"Invoice No. : {data['invoice_no']}")
    cnv.drawString(left + 6 * mm, top - 21 * mm, f"Invoice Date : {data['invoice_date'].strftime('%d-%b-%Y')}")
    cnv.drawString(left + 6 * mm, top - 26 * mm, f"Due Date : {data['due_date'].strftime('%d-%b-%Y')}")

    # Vendor / From
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawString(left, top - 40 * mm, "FROM:")
    cnv.setFont("Helvetica", 10)
    cnv.drawString(left, top - 46 * mm, data.get("vendor_name", ""))
    cnv.drawString(left, top - 52 * mm, data.get("vendor_address", ""))

    # Bill To
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawString(left, top - 66 * mm, "BILL TO:")
    cnv.setFont("Helvetica", 10)
    y = top - 72 * mm
    cnv.drawString(left, y, data["bill_to"])
    y -= 6 * mm
    if data.get("bill_address"):
        cnv.drawString(left, y, data["bill_address"])
        y -= 6 * mm

    # Line items
    items_top = y - 10 * mm
    cnv.setStrokeColor(accent)
    cnv.setLineWidth(0.5)
    cnv.line(left, items_top, left + usable_width, items_top)

    col1_x = left
    col3_x = left + usable_width * 0.85

    cnv.setFont("Helvetica-Bold", 10)
    cnv.drawString(col1_x, items_top - 8 * mm, "No")
    cnv.drawString(col1_x + 8 * mm, items_top - 8 * mm, "Item Description")
    cnv.drawString(col3_x - 18 * mm, items_top - 8 * mm, f"Amount ({data.get('currency_symbol', 'Rp')})")

    cnv.setFont("Helvetica", 10)
    y = items_top - 16 * mm
    idx = 1
    for it in data["items"]:
        if y < 40 * mm:
            cnv.showPage()
            y = height - 40 * mm
        cnv.drawString(col1_x, y, str(idx))
        draw_wrapped_string(
            cnv,
            f"{it.get('name', '')} - {it.get('desc', '')}",
            col1_x + 8 * mm,
            y + 2,
            max_width=(usable_width * 0.65),
        )
        cnv.drawRightString(
            left + usable_width - 6 * mm,
            y,
            f"{it.get('amount', 0):,.0f}"
        )
        y -= 8 * mm
        idx += 1

    # Total
    y -= 6 * mm
    cnv.setFont("Helvetica-Bold", 11)
    curr = data.get("currency_symbol", "Rp")
    cnv.drawRightString(
        left + usable_width - 6 * mm,
        y,
        f"TOTAL ({curr} {data['total']:,.0f})"
    )
    y -= 10 * mm

    # Remittance
    cnv.setFont("Helvetica-Bold", 10)
    cnv.drawString(left, y, "REMITTANCE INFORMATION")
    cnv.setFont("Helvetica", 9)
    y -= 6 * mm
    rem = data.get("remittance", {})
    cnv.drawString(left, y, f"Bank Account : {rem.get('bank', '')}")
    y -= 5 * mm
    cnv.drawString(left, y, f"Account Name : {rem.get('account_name', '')}")
    y -= 5 * mm
    cnv.drawString(left, y, f"Account No : {rem.get('account_no', '')}")
    y -= 5 * mm
    cnv.drawString(left, y, f"SWIFT Code : {rem.get('swift', '')}")

    cnv.setFont("Helvetica-Oblique", 8)
    cnv.setFillColor(colors.grey)
    cnv.drawString(left, 18 * mm, "") #if want to add a remark in the invoice PDF

    cnv.save()
    buffer.seek(0)
    return buffer.read()

def create_po_pdf_bytes(data: dict, template: str) -> bytes:
    buffer = BytesIO()
    cnv = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    left, right = 20*mm, 20*mm
    top = height - 20*mm
    usable = width - left - right

    # Colors follow same theme as invoice PDF
    if template == "cream":
        accent = colors.HexColor("#795757")
        text_color = colors.HexColor("#3B3030")
        header_bg = colors.HexColor("#FFF0D1")
    elif template == "pastel":
        accent = colors.HexColor("#D97D55")
        text_color = colors.HexColor("#6FA4AF")
        header_bg = colors.HexColor("#F4E9D7")
    else:
        accent = colors.HexColor("#948979")
        text_color = colors.HexColor("#222831")
        header_bg = colors.HexColor("#DFD0B8")

    # Header
    cnv.setFillColor(header_bg)
    cnv.rect(left, top - 30*mm, usable, 30*mm, fill=True, stroke=False)

    cnv.setFillColor(text_color)
    cnv.setFont("Helvetica-Bold", 18)
    cnv.drawString(left + 6*mm, top - 10*mm, "PURCHASE ORDER")

    cnv.setFont("Helvetica", 10)
    cnv.drawString(left + 6*mm, top - 17*mm, f"PO No. : {data['po_no']}")
    cnv.drawString(left + 6*mm, top - 23*mm, f"PO Date : {data['po_date'].strftime('%d-%b-%Y')}")

    # Vendor (PO TO)
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawString(left, top - 40*mm, "PO TO:")
    cnv.setFont("Helvetica", 10)
    cnv.drawString(left, top - 46*mm, data["vendor_name"])
    cnv.drawString(left, top - 52*mm, data["vendor_address"])

    # Issuer (Buyer)
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawString(left, top - 66*mm, "ISSUED BY:")
    cnv.setFont("Helvetica", 10)
    cnv.drawString(left, top - 72*mm, data["issuer_name"])
    cnv.drawString(left, top - 78*mm, data["issuer_address"])

    # Items
    items_top = top - 90*mm
    cnv.setStrokeColor(accent)
    cnv.line(left, items_top, left + usable, items_top)

    cnv.setFont("Helvetica-Bold", 10)
    cnv.drawString(left, items_top - 10, "No")
    cnv.drawString(left + 20*mm, items_top - 10, "Item")
    cnv.drawRightString(left + usable - 6*mm, items_top - 10, "Amount")

    y = items_top - 22
    cnv.setFont("Helvetica", 10)

    for i, it in enumerate(data["items"], 1):
        cnv.drawString(left, y, str(i))
        cnv.drawString(left + 20*mm, y, it["name"])
        cnv.drawRightString(left + usable - 6*mm, y, f"{it['amount']:,.0f}")
        y -= 12

    # TOTAL
    cnv.setFont("Helvetica-Bold", 12)
    cnv.drawRightString(
        left + usable - 6*mm,
        y - 15,
        f"TOTAL ({data['currency_symbol']} {data['total']:,.0f})"
    )

    cnv.save()
    buffer.seek(0)
    return buffer.read()

# -----------------------
# Streamlit UI
# -----------------------

st.set_page_config(page_icon="🌱", page_title="Paperbean", layout="wide")

# Simple cream-ish styling
st.markdown(
    """
    <style>
    .stApp { background: linear-gradient(180deg,#fbf7f3 0%,#fff 100%); }
    .small-muted { color: #6b6b6b; font-size: 12px; }
    </style>
    """,
    unsafe_allow_html=True
)

st.title("Paperbean — soft & tidy")
st.markdown(
    "<p style='font-size:16px; color:#7c7368; margin-top:-10px;'>"
    "Because good work deserves a graceful bill."
    "</p>",
    unsafe_allow_html=True
)
st.caption("Cream theme • 3 templates • automatic invoice numbers • history & PDF download")
tab_invoice, tab_po = st.tabs(["Invoice", "Purchase Order"])
with tab_po:
    st.header("Create a Purchase Order")

    # PO item list
    if "po_items" not in st.session_state:
        st.session_state.po_items = [{"name": "New item", "amount": 0}]

    def po_add_item():
        st.session_state.po_items.append({"name": "New item", "amount": 0})

    def po_remove_item():
        if len(st.session_state.po_items) > 1:
            st.session_state.po_items.pop()

    colA, colB = st.columns([2,1])

    with colA:
        vendor_name = st.text_input("PO To (Vendor)")
        vendor_address = st.text_area("Vendor Address")

        issuer_name = st.text_input("Issued by (Your name/company)")
        issuer_address = st.text_area("Issuer Address")

        po_date = st.date_input("PO Date", value=datetime.today().date())

        currency = st.selectbox("Currency", ["IDR (Rp)", "USD ($)", "EUR (€)", "SGD (S$)", "GBP (£)"])
        currency_symbol = currency.split("(")[1].replace(")", "")

        st.write("### Items")
        for i, it in enumerate(st.session_state.po_items):
            it["name"] = st.text_input(f"Item {i+1}", it["name"], key=f"po_name_{i}")
            it["amount"] = st.number_input(f"Amount {i+1}", 0, value=int(it["amount"]), key=f"po_amt_{i}")

        st.button("➕ Add item", on_click=po_add_item)
        st.button("➖ Remove item", on_click=po_remove_item)

    with colB:
        st.write("### Template")
        template_po = st.selectbox("PO Template", ["Cream Minimalist", "Playful Pastel", "Modern Monochrome"])

        save_po = st.checkbox("Save PO to server", value=True)

    submit_po = st.button("Generate PO")

    if submit_po:
        updated_items = []
        for i in range(len(st.session_state.po_items)):
            updated_items.append({
                "name": st.session_state.get(f"po_name_{i}", ""),
                "amount": float(st.session_state.get(f"po_amt_{i}", 0)),
            })

        total = sum(it["amount"] for it in updated_items)

        initials = make_initials(vendor_name)
        dt = datetime.combine(po_date, datetime.min.time())
        seq = get_next_po_sequence(initials, dt.year)
        po_no = build_po_number(initials, seq, dt)

        tpl_map = {
            "Cream Minimalist": "cream",
            "Playful Pastel": "pastel",
            "Modern Monochrome": "mono",
        }
        tpl_key = tpl_map.get(template_po, "cream")

        data = {
            "po_no": po_no,
            "po_date": dt,
            "vendor_name": vendor_name,
            "vendor_address": vendor_address,
            "issuer_name": issuer_name,
            "issuer_address": issuer_address,
            "items": updated_items,
            "total": total,
            "currency_symbol": currency_symbol,
        }

        pdf_bytes = create_po_pdf_bytes(data, tpl_key)
        filename = f"{po_no.replace('/', '-')}.pdf"

        # Upload to Supabase (bucket: pos)
        bucket = supabase.storage.from_("pos")
        bucket.upload(filename, pdf_bytes, {"content-type": "application/pdf"})
        pdf_url = bucket.get_public_url(filename)

        if save_po:
            save_po_record(
                vendor_name,
                initials,
                seq,
                po_no,
                dt,
                template_po,
                float(total),
                pdf_url,
                issuer_name,
                issuer_address,
                currency_symbol
            )

        st.success(f"PO {po_no} created.")
        st.download_button("Download PO PDF", pdf_bytes, filename)

# Sidebar: options & recent history
with st.sidebar:
    st.header("Options & history")
    template_choice = st.selectbox("Invoice template", ["Cream Minimalist", "Playful Pastel", "Modern Monochrome"])
    st.markdown("---")

st.markdown("## Create an invoice")

# --- Manage line items (outside form) ---
if "line_items" not in st.session_state or not isinstance(st.session_state.line_items, list):
    st.session_state.line_items = [{"name": "New item", "desc": "", "amount": 0}]

def add_item():
    st.session_state.line_items.append({"name": "New item", "desc": "", "amount": 0})

def remove_last():
    if len(st.session_state.line_items) > 1:
        st.session_state.line_items.pop()

st.write("### Item list controls")
col_add, col_remove = st.columns(2)
with col_add:
    st.button("➕ Add item", on_click=add_item)
with col_remove:
    st.button("➖ Remove last item", on_click=remove_last)

st.markdown("---")

# --- Form key for reset functionality ---
if "form_key" not in st.session_state:
    st.session_state.form_key = "invoice_form"

def reset_form():
    # Reset the form key to force remount
    st.session_state.form_key = f"invoice_form_{datetime.now().timestamp()}"
    # Reset line items
    st.session_state.line_items = [{"name": "New item", "desc": "", "amount": 0}]
    # Reset vendor & billing info
    for key in ("name", "bill_address", "vendor_name", "vendor_address", "bank", "account_name", "account_no", "swift"):
        st.session_state[key] = ""

# Reset button
st.button("🔄 Reset Form", on_click=reset_form)

# --- The actual form ---
with st.form("invoice_form"):
    colA, colB = st.columns([2, 1])

    with colA:
        name = st.text_input("Bill To — full name", value="")
        bill_address = st.text_area("Billing address (optional)", value="")
        invoice_date = st.date_input("Invoice date", value=datetime.today().date())
        due_add_days = st.selectbox("Due date offset", [7, 14, 30], index=0)
        due_date = st.date_input("Due date", value=(invoice_date + timedelta(days=due_add_days)))

        currency = st.selectbox(
            "Currency",
            ["IDR (Rp)", "USD ($)", "EUR (€)", "SGD (S$)", "GBP (£)"],
            index=0
        )
        
        # Extract symbol only (e.g. "Rp")
        currency_symbol = currency.split("(")[1].replace(")", "")

        st.markdown("**Itemized list**")
        for i, it in enumerate(st.session_state.line_items):
            it["name"] = st.text_input(f"Item name {i+1}", value=it.get("name", ""), key=f"name_{i}")
            it["desc"] = st.text_input(f"Description {i+1}", value=it.get("desc", ""), key=f"desc_{i}")
            it["amount"] = st.number_input(
                f"Amount ({currency_symbol}) {i+1}",
                min_value=0,
                value=int(float(it.get("amount") or 0)),
                step=1000,
                key=f"amt_{i}"
            )

    with colB:
        st.markdown("### Vendor / Issuer")
        vendor_name = st.text_input("Vendor name", value="")
        # Pull last remittance data for this vendor (if exists)
        previous_data = get_last_remittance(vendor_name) if vendor_name else {}

        vendor_address = st.text_area("Vendor address", value="")
    
        st.markdown("### Remittance")
        bank = st.text_input("Bank", value=previous_data.get("bank", ""))
        account_name = st.text_input("Account name", value=previous_data.get("account_name", ""))
        account_no = st.text_input("Account no", value=previous_data.get("account_no", ""))
        swift = st.text_input("SWIFT Code", value=previous_data.get("swift", ""))
    
        st.markdown("### Template preview & controls")
        st.markdown(f"**Current template:** {template_choice}")
        save_pdf = st.checkbox("Save PDF to server & log invoice", value=True)

    # ✅ Keep this button inside the `with st.form()` block
    submit = st.form_submit_button("Generate Invoice")

# -----------------------
# On submit: gather data, create PDF, save & show preview
# -----------------------
if submit:
    # 1️⃣ Sync all item inputs from widget state
    updated_items = []
    for i in range(len(st.session_state.line_items)):
        name_key = f"name_{i}"
        desc_key = f"desc_{i}"
        amt_key = f"amt_{i}"
        updated_items.append({
            "name": st.session_state.get(name_key, ""),
            "desc": st.session_state.get(desc_key, ""),
            "amount": float(st.session_state.get(amt_key, 0))
        })
    st.session_state.line_items = updated_items

    # 2️⃣ Clean/summarize for safety
    cleaned_items = [it for it in st.session_state.line_items if it.get("name")]

    # 3️⃣ Compute total from the cleaned list
    total = sum(float(it.get("amount", 0)) for it in cleaned_items)

    vendor_clean = (vendor_name or "").strip()
    vendor_initials = make_initials(vendor_clean)
    inv_dt = datetime.combine(invoice_date, datetime.min.time())
    seq = get_next_sequence(vendor_initials, inv_dt.year)
    invoice_no = build_invoice_number(vendor_initials, seq, inv_dt)

    tpl_map = {
        "Cream Minimalist": "cream",
        "Playful Pastel": "pastel",
        "Modern Monochrome": "mono"
    }
    tpl_key = tpl_map.get(template_choice, "cream")

    data = {
        "invoice_no": invoice_no,
        "invoice_date": inv_dt,
        "due_date": datetime.combine(due_date, datetime.min.time()),
        "bill_to": name or "Unnamed",
        "bill_address": bill_address,
        "items": cleaned_items,
        "total": total,
        "currency_symbol": currency_symbol,
        "remittance": {
            "bank": bank,
            "account_name": account_name,
            "account_no": account_no,
            "swift": swift
        },
        "vendor_name": vendor_name,
        "vendor_address": vendor_address,
    }

    try:
        pdf_bytes = create_pdf_bytes(data, tpl_key)
    except Exception as e:
        st.error(f"PDF generation failed: {e}")
        st.stop()

    # Create unique filename with timestamp to avoid 409 duplicate errors
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    pdf_filename = f"{invoice_no.replace('/', '-')}_{timestamp}.pdf"
    pdf_bytes = create_pdf_bytes(data, tpl_key)
    
    try:
        bucket = supabase.storage.from_("invoices")
    
        # Upload new version (no overwrite)
        res = bucket.upload(
            pdf_filename,
            pdf_bytes,
            {
                "content-type": "application/pdf",
                "upsert": False
            }
        )
    
        # Get public URL for Supabase Storage file
        pdf_url = bucket.get_public_url(pdf_filename)
    
    except Exception as e:
        st.error(f"Failed to upload PDF to Supabase: {e}")
        pdf_url = None
    
    # Save invoice record (store pdf_url instead of local pdf_path)
    if save_pdf:
        save_invoice_record(
        vendor_name,
        vendor_initials,
        seq,
        invoice_no,
        inv_dt,
        datetime.combine(due_date, datetime.min.time()),
        template_choice,
        float(total),
        pdf_url or "",
        bank,
        account_name,
        account_no,
        swift,
        currency_symbol
    )
        st.success(f"Saved invoice {invoice_no} and logged it.")

    # Preview + download button
    st.markdown("### Preview")
    colp1, colp2 = st.columns([2,1])
    with colp1:
        st.write(f"**{invoice_no}** — {vendor_clean}")
        st.write(f"Date: {invoice_date.strftime('%d-%b-%Y')}, Due: {due_date.strftime('%d-%b-%Y')}")
        st.write("Items:")
        for i, it in enumerate(cleaned_items, 1):
            st.write(f"{i}. {it['name']} — {it.get('desc','')} — {currency_symbol} {int(it['amount']):,}")
        st.write(f"**TOTAL: {currency_symbol} {int(total):,}**")
        st.write("Remittance:")
        st.write(f"{bank} • {account_name} • {account_no} • SWIFT: {swift}")

    with colp2:
        st.download_button("Download PDF", data=pdf_bytes, file_name=pdf_filename, mime="application/pdf")

# -----------------------
# Footer notes
# -----------------------
# -----------------------
# Footer / App info
# -----------------------
st.markdown(
    """
    ---
    <div style='text-align:center; color:#7c7368; font-size:13px;'>
        <b>Paperbean</b> • v3.5.0 — A soft & tidy invoice maker<br>
        © 2025 Paperbean — handcrafted utility for thoughtful creators.
    </div>
    """,
    unsafe_allow_html=True
)
