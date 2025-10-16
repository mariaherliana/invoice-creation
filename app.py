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

def save_invoice_record(name, initials, seq, invoice_no, invoice_date, due_date, template, total, pdf_path):
    data = {
        "name": name,
        "initials": initials,
        "seq": seq,
        "invoice_no": invoice_no,
        "invoice_date": invoice_date.isoformat(),
        "due_date": due_date.isoformat(),
        "template": template,
        "total": total,
        "pdf_path": str(pdf_url),
    }
    supabase.table("invoices").insert(data).execute()

def fetch_history(limit=100):
    res = supabase.table("invoices").select("*").order("created_at", desc=True).limit(limit).execute()
    return res.data or []

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
        accent = colors.HexColor("#9aa494")
        text_color = colors.HexColor("#5c4a3d")
        header_bg = colors.HexColor("#f6efe8")
    elif template == "pastel":
        accent = colors.HexColor("#f2c6d2")
        text_color = colors.HexColor("#5f4d7a")
        header_bg = colors.HexColor("#f7f3ff")
    else:
        accent = colors.HexColor("#2b6f77")
        text_color = colors.HexColor("#222222")
        header_bg = colors.HexColor("#f7f7f7")

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
    cnv.drawString(col3_x - 18 * mm, items_top - 8 * mm, "Amount (Rp)")

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
        cnv.drawRightString(left + usable_width - 6 * mm, y, f"{it.get('amount', 0):,.0f}")
        y -= 8 * mm
        idx += 1

    # Total
    y -= 6 * mm
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawRightString(left + usable_width - 6 * mm, y, f"TOTAL (Rp {data['total']:,.0f})")
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

# -----------------------
# Streamlit UI
# -----------------------

st.set_page_config(page_icon="🗒️🌱", page_title="Paperbean", layout="wide")

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

# Sidebar: options & recent history
with st.sidebar:
    st.header("Options & history")
    template_choice = st.selectbox("Invoice template", ["Cream Minimalist", "Playful Pastel", "Modern Monochrome"])
    st.markdown("---")
    st.subheader("Recent invoices")
    hist = fetch_history(10)
    if hist:
        for row in hist:
            iid, name_db, initials, seq, invoice_no, invoice_date, due_date, tpl, total_db, pdf_path, created_at = row
            col1, col2 = st.columns([3,1])
            with col1:
                st.markdown(f"**{invoice_no}** — {name_db}")
                try:
                    total_display = f"Rp {float(total_db):,.0f}"
                except Exception:
                    total_display = "Rp —"
                st.markdown(
                    f"<div class='small-muted'>{invoice_date[:10]} • {tpl} • {total_display}</div>",
                    unsafe_allow_html=True,
                )
            with col2:
                p = Path(pdf_path)
                if p.exists():
                    with open(p, "rb") as f:
                        raw = f.read()
                    st.download_button("⬇️", raw, file_name=p.name, key=f"dl_{iid}", use_container_width=True)
                else:
                    st.write("—")

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

# --- The actual form ---
with st.form("invoice_form"):
    colA, colB = st.columns([2, 1])

    with colA:
        name = st.text_input("Bill To — full name", value="")
        bill_address = st.text_area("Billing address (optional)", value="")
        invoice_date = st.date_input("Invoice date", value=datetime.today().date())
        due_add_days = st.selectbox("Due date offset", [7, 14, 30], index=0)
        due_date = st.date_input("Due date", value=(invoice_date + timedelta(days=due_add_days)))

        st.markdown("**Itemized list**")
        for i, it in enumerate(st.session_state.line_items):
            it["name"] = st.text_input(f"Item name {i+1}", value=it.get("name", ""), key=f"name_{i}")
            it["desc"] = st.text_input(f"Description {i+1}", value=it.get("desc", ""), key=f"desc_{i}")
            it["amount"] = st.number_input(
                f"Amount (Rp) {i+1}",
                min_value=0,
                value=int(float(it.get("amount") or 0)),
                step=1000,
                key=f"amt_{i}"
            )

    with colB:
        st.markdown("### Remittance")
        bank = st.text_input("Bank", value="")
        account_name = st.text_input("Account name", value="")
        account_no = st.text_input("Account no", value="")
        swift = st.text_input("SWIFT Code", value="")

        st.markdown("### Template preview & controls")
        st.markdown(f"**Current template:** {template_choice}")
        save_pdf = st.checkbox("Save PDF to server & log invoice", value=True)

        st.markdown("### Vendor / Issuer")
        vendor_name = st.text_input("Vendor name", value="")
        vendor_address = st.text_area("Vendor address", value="")

    # ✅ Keep this button inside the `with st.form()` block
    submit = st.form_submit_button("Generate Invoice")

def reset_form():
    for k in list(st.session_state.keys()):
        if k.startswith(("name_", "desc_", "amt_")) or k in ["line_items"]:
            del st.session_state[k]
    st.session_state.line_items = [{"name": "New item", "desc": "", "amount": 0}]
    st.session_state["reset_trigger"] = True  # flag

if st.button("🔄 Reset Form", on_click=reset_form):
    pass  # callback runs here

if st.session_state.get("reset_trigger"):
    st.session_state["reset_trigger"] = False
    st.rerun()

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
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    pdf_filename = f"{invoice_no.replace('/', '-')}_{timestamp}.pdf"
    pdf_bytes = create_pdf_bytes(data, tpl_key)
    
    try:
        bucket = supabase.storage.from_("invoices")
    
        # Upload new version (no overwrite)
        res = bucket.upload(
            pdf_filename,
            pdf_bytes,
            {"content-type": "application/pdf"}
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
            pdf_url or ""
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
            st.write(f"{i}. {it['name']} — {it.get('desc','')} — Rp {int(it['amount']):,}")
        st.write(f"**TOTAL: Rp {int(total):,}**")
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
        <b>Paperbean</b> • v3.0.0 — A soft & tidy invoice maker<br>
        © 2025 Paperbean — handcrafted utility for thoughtful creators.
    </div>
    """,
    unsafe_allow_html=True
)
