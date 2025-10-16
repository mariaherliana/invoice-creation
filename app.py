# app.py
"""
Streamlit invoice maker
- 3 templates (Cream Minimalist, Playful Pastel, Modern Monochrome)
- Auto invoice numbering per person with logs in SQLite
- Save PDF files to ./invoices and show history with download links
- Cream theme UI touches
"""

import streamlit as st
from datetime import datetime, timedelta
import sqlite3
import os
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from io import BytesIO
import math
import textwrap

# -----------------------
# Helpers
# -----------------------

DB_PATH = "invoices.db"
INVOICE_DIR = Path("invoices")
INVOICE_DIR.mkdir(exist_ok=True)

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute(
        """CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            initials TEXT NOT NULL,
            seq INTEGER NOT NULL,
            invoice_no TEXT NOT NULL,
            invoice_date TEXT,
            due_date TEXT,
            template TEXT,
            total REAL,
            pdf_path TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.commit()
    return conn

conn = init_db()

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
    c = conn.cursor()
    c.execute(
        "SELECT seq FROM invoices WHERE initials = ? AND strftime('%Y', invoice_date) = ? ORDER BY seq DESC LIMIT 1",
        (initials, str(year))
    )
    row = c.fetchone()
    if row:
        return row[0] + 1
    return 1

def build_invoice_number(initials: str, seq: int, dt: datetime):
    seq_s = f"{seq:03d}"
    roman = to_roman_month(dt)
    year = dt.year
    return f"{initials}/{seq_s}/{roman}/{year}"

def save_invoice_record(name, initials, seq, invoice_no, invoice_date, due_date, template, total, pdf_path):
    c = conn.cursor()
    c.execute(
        "INSERT INTO invoices (name, initials, seq, invoice_no, invoice_date, due_date, template, total, pdf_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, initials, seq, invoice_no, invoice_date.isoformat(), due_date.isoformat(), template, total, str(pdf_path))
    )
    conn.commit()
    return c.lastrowid

def fetch_history(limit=100):
    c = conn.cursor()
    c.execute("SELECT id, name, initials, seq, invoice_no, invoice_date, due_date, template, total, pdf_path, created_at FROM invoices ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = c.fetchall()
    return rows

# -----------------------
# PDF generation using reportlab
# -----------------------

def draw_wrapped_string(cnv, text, x, y, max_width, leading=12):
    """
    Draw text wrapped to fit max_width on canvas `cnv` starting at x, y (from top).
    returns new y position after drawing.
    """
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
      - invoice_no, invoice_date (datetime), due_date (datetime), bill_to, line_items(list of dict{name,desc,amount}),
      - remittance dict(bank, account_name, account_no, swift)
    template: one of 'cream', 'pastel', 'mono'
    """
    buffer = BytesIO()
    cnv = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Margins
    left = 20 * mm
    right = 20 * mm
    top = height - 20 * mm
    usable_width = width - left - right

    # Colors per template
    if template == "cream":
        accent = colors.HexColor("#9aa494")  # muted sage
        text_color = colors.HexColor("#5c4a3d")
        header_bg = colors.HexColor("#f6efe8")
    elif template == "pastel":
        accent = colors.HexColor("#f2c6d2")  # pastel pink (used sparingly)
        text_color = colors.HexColor("#5f4d7a")
        header_bg = colors.HexColor("#f7f3ff")
    else:  # mono
        accent = colors.HexColor("#2b6f77")
        text_color = colors.HexColor("#222222")
        header_bg = colors.HexColor("#f7f7f7")

    # Header
    cnv.setFillColor(header_bg)
    cnv.rect(left, top - 30 * mm, usable_width, 30 * mm, fill=True, stroke=False)

    cnv.setFillColor(text_color)
    cnv.setFont("Helvetica-Bold", 18)
    cnv.drawString(left + 6 * mm, top - 10 * mm, "INVOICE")

    cnv.setFont("Helvetica", 10)
    cnv.drawString(left + 6 * mm, top - 16 * mm, f"Invoice No. : {data['invoice_no']}")
    cnv.drawString(left + 6 * mm, top - 21 * mm, f"Invoice Date : {data['invoice_date'].strftime('%d-%b-%Y')}")
    cnv.drawString(left + 6 * mm, top - 26 * mm, f"Due Date : {data['due_date'].strftime('%d-%b-%Y')}")

    # Bill To
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawString(left, top - 42 * mm, "BILL TO:")
    cnv.setFont("Helvetica", 10)
    y = top - 48 * mm
    cnv.drawString(left, y, data['bill_to'])
    y -= 6 * mm
    if data.get("bill_address"):
        cnv.drawString(left, y, data['bill_address'])
        y -= 6 * mm

    # Line items header
    items_top = top - 70 * mm
    cnv.setStrokeColor(accent)
    cnv.setLineWidth(0.5)
    cnv.line(left, items_top, left + usable_width, items_top)

    # Column headings
    col1_x = left
    col2_x = left + usable_width * 0.55
    col3_x = left + usable_width * 0.85

    cnv.setFont("Helvetica-Bold", 10)
    cnv.drawString(col1_x, items_top - 8 * mm, "No")
    cnv.drawString(col1_x + 8 * mm, items_top - 8 * mm, "Item Description")
    cnv.drawString(col3_x - 18 * mm, items_top - 8 * mm, "Amount (Rp)")

    # Items
    cnv.setFont("Helvetica", 10)
    y = items_top - 16 * mm
    idx = 1
    for it in data['items']:
        if y < 40 * mm:
            cnv.showPage()
            cnv.setFont("Helvetica", 10)
            y = height - 40 * mm
        cnv.drawString(col1_x, y, str(idx))
        draw_wrapped_string(cnv, f"{it.get('name','')} - {it.get('desc','')}", col1_x + 8 * mm, y + 2, max_width=(usable_width * 0.65))
        amount_text = f"{it.get('amount', 0):,.0f}"
        cnv.drawRightString(left + usable_width - 6 * mm, y, amount_text)
        y -= 8 * mm
        idx += 1

    # Totals
    y -= 6 * mm
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawRightString(left + usable_width - 6 * mm, y, f"TOTAL (Rp {data['total']:,.0f})")
    y -= 10 * mm

    # Remittance
    cnv.setFont("Helvetica-Bold", 10)
    cnv.drawString(left, y, "REMITTANCE INFORMATION")
    cnv.setFont("Helvetica", 9)
    y -= 6 * mm
    rem = data.get('remittance', {})
    cnv.drawString(left, y, f"Bank Account : {rem.get('bank','')}")
    y -= 5 * mm
    cnv.drawString(left, y, f"Account Name : {rem.get('account_name','')}")
    y -= 5 * mm
    cnv.drawString(left, y, f"Account No : {rem.get('account_no','')}")
    y -= 5 * mm
    cnv.drawString(left, y, f"SWIFT Code : {rem.get('swift','')}")
    y -= 10 * mm

    # Footer small watermark text
    cnv.setFont("Helvetica-Oblique", 8)
    cnv.setFillColor(colors.grey)
    cnv.drawString(left, 18 * mm, "Generated by Streamlit Invoice Maker")

    cnv.showPage()
    cnv.save()
    buffer.seek(0)
    return buffer.read()

# -----------------------
# Streamlit UI
# -----------------------

st.set_page_config(page_title="Invoice Maker", layout="wide")

# Minimal cream theme injection (Streamlit's CSS)
st.markdown(
    """
    <style>
    .reportview-container {
        background: #fbf7f3;
    }
    .stApp {
        background: linear-gradient(180deg, #fbf7f3 0%, #fff 100%);
    }
    .invoice-card {
        background: #fffdfa;
        border-radius: 12px;
        padding: 16px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
    }
    .small-muted { color: #6b6b6b; font-size: 12px; }
    </style>
    """, unsafe_allow_html=True
)

st.title("Invoice Maker — soft & tidy")
st.caption("Cream theme • 3 templates • automatic invoice numbers • history & PDF download")

# Side: options and history
with st.sidebar:
    st.header("Options & history")
    template_choice = st.selectbox("Invoice template", ["Cream Minimalist", "Playful Pastel", "Modern Monochrome"])
    st.markdown("---")
    st.subheader("Recent invoices")
    hist = fetch_history(10)
    if hist:
        for row in hist:
            iid, name, initials, seq, invoice_no, invoice_date, due_date, tpl, total, pdf_path, created_at = row
            created_at_display = created_at.split(".")[0] if created_at else ""
            col1, col2 = st.columns([3,1])
            with col1:
                st.markdown(f"**{invoice_no}** — {name}  ")
                st.markdown(f"<div class='small-muted'>{invoice_date[:10]} • {tpl} • Rp {total:,.0f}</div>", unsafe_allow_html=True)
            with col2:
                # Provide download if file exists
                p = Path(pdf_path)
                if p.exists():
                    with open(p, "rb") as f:
                        data = f.read()
                    st.download_button("⬇️", data, file_name=p.name, key=f"dl_{iid}", use_container_width=True)
                else:
                    st.write("—")

st.markdown("## Create an invoice")

# --- Manage line items outside the form (so callbacks are allowed) ---
if "items" not in st.session_state:
    st.session_state.items = [
        {"name": "Retainer Fee for September 2025", "desc": "", "amount": 5000000}
    ]

def add_item():
    st.session_state.items.append({"name": "New item", "desc": "", "amount": 0})

def remove_last():
    if len(st.session_state.items) > 1:
        st.session_state.items.pop()

col_add, col_remove = st.columns(2)
with col_add:
    st.button("➕ Add item", on_click=add_item)
with col_remove:
    st.button("➖ Remove last item", on_click=remove_last)

st.markdown("---")

# --- The actual form (no callbacks inside) ---
with st.form("invoice_form"):
    colA, colB = st.columns([2, 1])
    with colA:
        name = st.text_input("Bill To — full name", value="Maria Herliana")
        bill_address = st.text_area("Billing address (optional)", value="YESUNDERBAR Pte. Ltd.")
        invoice_date = st.date_input("Invoice date", value=datetime.today().date())
        due_add_days = st.selectbox("Due date offset", [7, 14, 30], index=0)
        due_date = st.date_input("Due date", value=(invoice_date + timedelta(days=due_add_days)))

        st.markdown("**Itemized list**")
        for i, it in enumerate(st.session_state.items):
            st.markdown(f"**Item {i+1}**")
            it["name"] = st.text_input(f"Item name {i+1}", value=it.get("name", ""), key=f"name_{i}")
            it["desc"] = st.text_input(f"Description {i+1}", value=it.get("desc", ""), key=f"desc_{i}")
            it["amount"] = st.number_input(
                f"Amount (Rp) {i+1}",
                min_value=0,
                value=int(it.get("amount", 0)),
                step=1000,
                key=f"amt_{i}"
            )

    with colB:
        st.markdown("### Remittance")
        bank = st.text_input("Bank", value="BCA")
        account_name = st.text_input("Account name", value=name)
        account_no = st.text_input("Account no", value="5385153306")
        swift = st.text_input("SWIFT Code", value="CENAIDJA")

        st.markdown("### Template preview & controls")
        st.markdown(f"**Current template:** {template_choice}")
        save_pdf = st.checkbox("Save PDF to server & log invoice", value=True)

    # ✅ Only one button triggers the form submission
    submit = st.form_submit_button("Generate Invoice")

    # end form columns

# On submit: compute invoice number, create PDF, show preview and download
if submit:
    # Build initials and seq
    name_clean = name.strip()
    initials = make_initials(name_clean)
    inv_dt = datetime.combine(invoice_date, datetime.min.time())
    seq = get_next_sequence(initials, inv_dt.year)
    invoice_no = build_invoice_number(initials, seq, inv_dt)

    # compute total
    total = sum([float(it.get("amount",0)) for it in st.session_state.items])

    # chosen template key map
    tpl_map = {
        "Cream Minimalist": "cream",
        "Playful Pastel": "pastel",
        "Modern Monochrome": "mono"
    }
    tpl_key = tpl_map.get(template_choice, "cream")

    # prepare data for PDF and preview
    data = {
        "invoice_no": invoice_no,
        "invoice_date": inv_dt,
        "due_date": datetime.combine(due_date, datetime.min.time()),
        "bill_to": name_clean,
        "bill_address": bill_address,
        "items": st.session_state.items,
        "total": total,
        "remittance": {
            "bank": bank,
            "account_name": account_name,
            "account_no": account_no,
            "swift": swift
        }
    }

    # Make PDF bytes
    try:
        pdf_bytes = create_pdf_bytes(data, tpl_key)
    except Exception as e:
        st.error(f"PDF generation failed: {e}")
        st.stop()

    # Save PDF file if desired
    pdf_filename = f"{invoice_no.replace('/','-')}.pdf"
    pdf_path = INVOICE_DIR / pdf_filename
    if save_pdf:
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
        # Save DB record
        save_invoice_record(name_clean, initials, seq, invoice_no, inv_dt, datetime.combine(due_date, datetime.min.time()), template_choice, total, pdf_path)
        st.success(f"Saved invoice {invoice_no} and logged it.")

    # Preview area
    st.markdown("### Preview")
    colp1, colp2 = st.columns([2,1])
    with colp1:
        # Show a small embedded preview (pdf blob as download and link)
        st.write(f"**{invoice_no}** — {name_clean}")
        st.write(f"Date: {invoice_date.strftime('%d-%b-%Y')}, Due: {due_date.strftime('%d-%b-%Y')}")
        st.write("Items:")
        for i, it in enumerate(st.session_state.items, 1):
            st.write(f"{i}. {it['name']} — {it.get('desc','')} — Rp {int(it['amount']):,}")
        st.write(f"**TOTAL: Rp {int(total):,}**")
        st.write("Remittance:")
        st.write(f"{bank} • {account_name} • {account_no} • SWIFT: {swift}")

    with colp2:
        st.download_button("Download PDF", data=pdf_bytes, file_name=pdf_filename, mime="application/pdf")

    st.markdown("---")
    st.info("If you want different styling for the printable PDF later, we can extend the PDF renderer to place logos, use different fonts, or render HTML + headless conversion.")

# Small footer / hints
st.markdown(
    """
    ---
    **Notes & behavior**
    - Invoice number format: `INITIALS/###/MONTH_ROMAN/YEAR` (e.g. `MH/002/XI/2025`).
    - Sequence increments per `INITIALS` and `YEAR`. Saved invoices are stored in `./invoices` and logged in `invoices.db`.
    - The PDF renderer uses `reportlab` – modest but reliable. We can upgrade to a prettier HTML->PDF flow later.
    """
)

