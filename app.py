# app.py
"""
Streamlit invoice maker
- 3 templates (Cream Minimalist, Playful Pastel, Modern Monochrome)
- Auto invoice numbering per vendor with logs in Supabase
- Save PDF files to Supabase Storage
- PO creation feature
- Cream theme UI
"""

import streamlit as st
from datetime import datetime, timedelta
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from io import BytesIO
from supabase import create_client
import base64

# -----------------------
# Supabase Setup
# -----------------------

SUPABASE_URL = st.secrets["SUPABASE_URL"]
SUPABASE_KEY = st.secrets["SUPABASE_KEY"]
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# -----------------------
# Helpers
# -----------------------

ROMAN = {
    1:"I",2:"II",3:"III",4:"IV",5:"V",6:"VI",
    7:"VII",8:"VIII",9:"IX",10:"X",11:"XI",12:"XII"
}


def to_roman_month(dt: datetime):
    return ROMAN.get(dt.month, str(dt.month))


def make_initials(name: str):
    parts = [p for p in name.strip().split() if p]
    if len(parts) == 0:
        return "XX"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def get_next_sequence(initials: str, year: int):
    query = supabase.table("invoices") \
                    .select("seq, invoice_date") \
                    .eq("initials", initials) \
                    .execute()
    rows = query.data
    if not rows:
        return 1
    filtered = [r for r in rows if str(year) in (r["invoice_date"] or "")]
    if not filtered:
        return 1
    return max(r["seq"] for r in filtered) + 1


def build_invoice_number(initials, seq, dt):
    return f"{seq:03d}/INV-{initials}/{to_roman_month(dt)}/{dt.year}"

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
        "pdf_path": pdf_url,
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
    res = supabase.table("invoices") \
                  .select("bank, account_name, account_no, swift") \
                  .eq("name", name) \
                  .order("created_at", desc=True) \
                  .limit(1) \
                  .execute()
    if res.data:
        row = res.data[0]
        return {
            "bank": row.get("bank", ""),
            "account_name": row.get("account_name", ""),
            "account_no": row.get("account_no", ""),
            "swift": row.get("swift", "")
        }
    return {}

# -----------------------
# PO Helpers
# -----------------------

def get_next_po_sequence(initials: str, year: int):
    resp = supabase.table("purchase_orders") \
                   .select("seq, po_date") \
                   .eq("initials", initials) \
                   .execute()
    rows = resp.data
    if not rows:
        return 1
    filtered = [r for r in rows if str(year) in (r["po_date"] or "")]
    if not filtered:
        return 1
    return max(r["seq"] for r in filtered) + 1


def build_po_number(initials, seq, dt):
    return f"{seq:03d}/PO-{initials}/{to_roman_month(dt)}/{dt.year}"

def save_po_to_supabase(
    pdf_bytes: bytes,
    filename: str,
    po_payload: dict
) -> str:
    bucket = supabase.storage.from_("pos")

    # Upload PDF
    bucket.upload(
        path=filename,
        file=pdf_bytes,
        file_options={"content-type": "application/pdf"}
    )

    pdf_url = bucket.get_public_url(filename)["publicUrl"]

    # Insert DB row
    res = supabase.table("purchase_orders").insert({
        **po_payload,
        "pdf_url": pdf_url,
    }).execute()

    if res.error:
        bucket.remove([filename])
        raise RuntimeError(res.error)

    return pdf_url

# -----------------------
# PDF GENERATION
# -----------------------

def create_pdf_bytes(data: dict, template: str):
    buffer = BytesIO()
    cnv = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    left, right = 20*mm, 20*mm
    top = height - 20*mm
    usable = width - left - right

    # Template colors
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
    cnv.drawString(left + 6*mm, top - 10*mm, "INVOICE")

    cnv.setFont("Helvetica", 10)
    cnv.drawString(left + 6*mm, top - 17*mm, f"Invoice No : {data['invoice_no']}")
    cnv.drawString(left + 6*mm, top - 23*mm, f"Invoice Date : {data['invoice_date'].strftime('%d-%b-%Y')}")
    cnv.drawString(left + 6*mm, top - 29*mm, f"Due Date : {data['due_date'].strftime('%d-%b-%Y')}")

    # Vendor
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawString(left, top - 45*mm, "FROM:")
    cnv.setFont("Helvetica", 10)
    cnv.drawString(left, top - 51*mm, data["vendor_name"])
    cnv.drawString(left, top - 57*mm, data["vendor_address"])

    # Bill To
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawString(left, top - 70*mm, "BILL TO:")
    cnv.setFont("Helvetica", 10)
    cnv.drawString(left, top - 76*mm, data["bill_to"])
    cnv.drawString(left, top - 82*mm, data["bill_address"])

    # Items Table
    items_top = top - 95*mm
    cnv.setStrokeColor(accent)
    cnv.line(left, items_top, left + usable, items_top)

    cnv.setFont("Helvetica-Bold", 10)
    cnv.drawString(left, items_top - 10, "No")
    cnv.drawString(left + 10*mm, items_top - 10, "Item")
    cnv.drawRightString(left + usable - 6*mm, items_top - 10, "Amount")

    y = items_top - 25
    cnv.setFont("Helvetica", 10)

    for i, it in enumerate(data["items"], 1):
        cnv.drawString(left, y, str(i))
        cnv.drawString(left + 10*mm, y, f"{it['name']} {it.get('desc','')}")
        cnv.drawRightString(left + usable - 6*mm, y, f"{int(it['amount']):,}")
        y -= 12

    # Total
    cnv.setFont("Helvetica-Bold", 12)
    cnv.drawRightString(left + usable - 6*mm, y - 15,
                        f"TOTAL ({data['currency_symbol']} {int(data['total']):,})")

    # Remittance
    y -= 35
    cnv.setFont("Helvetica-Bold", 10)
    cnv.drawString(left, y, "REMITTANCE INFORMATION")
    cnv.setFont("Helvetica", 9)
    y -= 10
    r = data["remittance"]
    cnv.drawString(left, y, f"Bank: {r.get('bank','')}")
    y -= 10
    cnv.drawString(left, y, f"Account Name: {r.get('account_name','')}")
    y -= 10
    cnv.drawString(left, y, f"Account No: {r.get('account_no','')}")
    y -= 10
    cnv.drawString(left, y, f"SWIFT: {r.get('swift','')}")

    cnv.save()
    buffer.seek(0)
    return buffer.read()

# -----------------------
# Streamlit UI Setup
# -----------------------

st.set_page_config(page_title="Paperbean", page_icon="🌱", layout="wide")

st.markdown("""
<style>
.stApp { background: linear-gradient(180deg,#fbf7f3 0%,#fff 100%); }
.small-muted { color:#6b6b6b; font-size:12px; }
</style>
""", unsafe_allow_html=True)

st.title("Paperbean — soft & tidy")
st.caption("Cream theme • 3 templates • automatic invoice numbers • PO mode • history")

tab_invoice, tab_po = st.tabs(["Invoice", "Purchase Order"])

# =====================================================
# PURCHASE ORDER TAB
# =====================================================
def create_po_pdf_bytes(data: dict, template: str) -> bytes:
    """
    Simple PO PDF renderer — mirrors invoice style but uses PO fields:
    data should contain: po_no, po_date (datetime), vendor_name, vendor_address,
                         issuer_name, issuer_address, items (list of {name,amount}),
                         total, currency_symbol
    """
    buffer = BytesIO()
    cnv = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    left, right = 20 * mm, 20 * mm
    top = height - 20 * mm
    usable = width - left - right

    # Colors per template (keep same palette mapping as invoice)
    if template == "cream":
        accent = colors.HexColor("#795757")
        text_color = colors.HexColor("#3B3030")
        header_bg = colors.HexColor("#FFF0D1")
    elif template == "pastel":
        accent = colors.HexColor("#D97D55")
        text_color = colors.HexColor("#6FA4AF")
        header_bg = colors.HexColor("#F4E9D7")
    else:  # mono
        accent = colors.HexColor("#948979")
        text_color = colors.HexColor("#222831")
        header_bg = colors.HexColor("#DFD0B8")

    # Header band
    cnv.setFillColor(header_bg)
    cnv.rect(left, top - 30 * mm, usable, 30 * mm, fill=True, stroke=False)

    cnv.setFillColor(text_color)
    cnv.setFont("Helvetica-Bold", 18)
    cnv.drawString(left + 6 * mm, top - 10 * mm, "PURCHASE ORDER")

    cnv.setFont("Helvetica", 10)
    cnv.drawString(left + 6 * mm, top - 17 * mm, f"PO No. : {data.get('po_no','')}")
    po_date = data.get("po_date")
    if isinstance(po_date, datetime):
        cnv.drawString(left + 6 * mm, top - 23 * mm, f"PO Date : {po_date.strftime('%d-%b-%Y')}")
    else:
        cnv.drawString(left + 6 * mm, top - 23 * mm, f"PO Date : {str(po_date)}")

    # Vendor (PO TO)
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawString(left, top - 40 * mm, "PO TO:")
    cnv.setFont("Helvetica", 10)
    cnv.drawString(left, top - 46 * mm, data.get("vendor_name", ""))
    cnv.drawString(left, top - 52 * mm, data.get("vendor_address", ""))

    # Issuer (Buyer)
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawString(left, top - 66 * mm, "ISSUED BY:")
    cnv.setFont("Helvetica", 10)
    cnv.drawString(left, top - 72 * mm, data.get("issuer_name", ""))
    cnv.drawString(left, top - 78 * mm, data.get("issuer_address", ""))

    # Items table header
    items_top = top - 92 * mm
    cnv.setStrokeColor(accent)
    cnv.setLineWidth(0.5)
    cnv.line(left, items_top, left + usable, items_top)

    cnv.setFont("Helvetica-Bold", 10)
    cnv.drawString(left, items_top - 8 * mm, "No")
    cnv.drawString(left + 12 * mm, items_top - 8 * mm, "Item Description")
    cnv.drawRightString(left + usable - 6 * mm, items_top - 8 * mm, f"Amount ({data.get('currency_symbol','Rp')})")

    # Items
    y = items_top - 16 * mm
    cnv.setFont("Helvetica", 10)
    for idx, it in enumerate(data.get("items", []), 1):
        if y < 40 * mm:
            cnv.showPage()
            y = height - 40 * mm
        cnv.drawString(left, y, str(idx))
        cnv.drawString(left + 12 * mm, y, it.get("name", ""))
        cnv.drawRightString(left + usable - 6 * mm, y, f"{int(it.get('amount',0)):,.0f}")
        y -= 8 * mm

    # Total
    y -= 8 * mm
    cnv.setFont("Helvetica-Bold", 11)
    cnv.drawRightString(left + usable - 6 * mm, y, f"TOTAL ({data.get('currency_symbol','Rp')} {int(data.get('total',0)):,.0f})")

    # Finish
    cnv.save()
    buffer.seek(0)
    return buffer.read()

with tab_po:
    st.header("Create a Purchase Order")

    # Session state
    if "po_items" not in st.session_state:
        st.session_state.po_items = [{"name": "New item", "amount": 0}]

    def po_add_item():
        st.session_state.po_items.append({"name": "New item", "amount": 0})

    def po_remove_item():
        if len(st.session_state.po_items) > 1:
            st.session_state.po_items.pop()

    colA, colB = st.columns([2, 1])

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

        st.button("➕ Add item", key="po_add_item", on_click=po_add_item)
        st.button("➖ Remove item", key="po_remove_item", on_click=po_remove_item)

    with colB:
        template_po = st.selectbox("Template", ["Cream Minimalist", "Playful Pastel", "Modern Monochrome"])
        save_po = st.checkbox("Save PO to server", value=True)

    submit_po = st.button("Generate PO")

    if submit_po:
        updated_items = [{
            "name": st.session_state[f"po_name_{i}"],
            "amount": float(st.session_state[f"po_amt_{i}"])
        } for i in range(len(st.session_state.po_items))]

        total = sum(it["amount"] for it in updated_items)

        initials = make_initials(vendor_name)
        dt = datetime.combine(po_date, datetime.min.time())
        seq = get_next_po_sequence(initials, dt.year)
        po_no = build_po_number(initials, seq, dt)

        tpl_map = {
            "Cream Minimalist": "cream",
            "Playful Pastel": "pastel",
            "Modern Monochrome": "mono"
        }
        tpl_key = tpl_map[template_po]

        data = {
            "po_no": po_no,
            "po_date": dt,
            "vendor_name": vendor_name,
            "vendor_address": vendor_address,
            "issuer_name": issuer_name,
            "issuer_address": issuer_address,
            "items": updated_items,
            "total": total,
            "currency_symbol": currency_symbol
        }

        pdf_bytes = create_po_pdf_bytes(data, tpl_key)
        filename = f"{po_no.replace('/', '-')}.pdf"
        
        if save_po:
            try:
                pdf_url = save_po_to_supabase(
                    pdf_bytes=pdf_bytes,
                    filename=filename,
                    po_payload={
                        "vendor_name": vendor_name,
                        "vendor_address": vendor_address,
                        "issuer_name": issuer_name,
                        "issuer_address": issuer_address,
                        "initials": initials,
                        "seq": seq,
                        "po_no": po_no,
                        "po_date": dt.isoformat(),
                        "template": tpl_key,
                        "total": total,
                        "currency": currency_symbol,
                    }
                )
            except Exception as e:
                st.error(f"Failed to save PO: {e}")
                st.stop()

        st.markdown("### Preview")

        colp1, colp2 = st.columns([2, 1])
        
        with colp1:
            st.write(f"**{po_no}**")
            st.write(f"PO Date: {dt.strftime('%d-%b-%Y')}")
            st.write("PO To:")
            st.write(vendor_name)
            if vendor_address:
                st.write(vendor_address)
        
            st.write("Issued By:")
            st.write(issuer_name)
            if issuer_address:
                st.write(issuer_address)
        
            st.write("Items:")
            for i, it in enumerate(updated_items, 1):
                st.write(
                    f"{i}. {it['name']} — {currency_symbol} {int(it['amount']):,}"
                )
        
            st.write(f"**TOTAL: {currency_symbol} {int(total):,}**")
        
        with colp2:
            st.download_button(
                "Download PO PDF",
                data=pdf_bytes,
                file_name=filename,
                mime="application/pdf"
            )

# =====================================================
# INVOICE TAB (clean, corrected)
# =====================================================

with tab_invoice:
    st.header("Create an Invoice")

    # Line items
    if "line_items" not in st.session_state:
        st.session_state.line_items = [{"name": "New item", "desc": "", "amount": 0}]

    def add_item():
        st.session_state.line_items.append({"name": "New item", "desc": "", "amount": 0})

    def remove_last():
        if len(st.session_state.line_items) > 1:
            st.session_state.line_items.pop()

    st.write("### Item list controls")
    col_add, col_remove = st.columns(2)
    with col_add:
        st.button("➕ Add item", key="inv_add_item", on_click=add_item)
    with col_remove:
        st.button("➖ Remove last item", key="inv_remove_item", on_click=remove_last)

    st.markdown("---")

    # Reset form
    if "form_key" not in st.session_state:
        st.session_state.form_key = "invoice_form"

    def reset_form():
        st.session_state.form_key = f"invoice_form_{datetime.now().timestamp()}"
        st.session_state.line_items = [{"name": "New item", "desc": "", "amount": 0}]
        for key in ("name", "bill_address", "vendor_name", "vendor_address", "bank",
                    "account_name", "account_no", "swift"):
            st.session_state[key] = ""

    st.button("🔄 Reset Form", on_click=reset_form)

    # Form begins
    with st.form("invoice_form"):
        colA, colB = st.columns([2, 1])

        # Left side
        with colA:
            name = st.text_input("Bill To — full name")
            bill_address = st.text_area("Billing address (optional)")
            invoice_date = st.date_input("Invoice date", datetime.today().date())
            due_add_days = st.selectbox("Due date offset", [7, 14, 30])
            due_date = invoice_date + timedelta(days=due_add_days)

            currency = st.selectbox(
                "Currency",
                ["IDR (Rp)", "USD ($)", "EUR (€)", "SGD (S$)", "GBP (£)"]
            )
            currency_symbol = currency.split("(")[1].replace(")", "")

            st.markdown("**Itemized list**")
            for i, it in enumerate(st.session_state.line_items):
                it["name"] = st.text_input(f"Item {i+1}", it["name"], key=f"name_{i}")
                it["desc"] = st.text_input(f"Desc {i+1}", it["desc"], key=f"desc_{i}")
                it["amount"] = st.number_input(
                    f"Amount {i+1}",
                    min_value=0,
                    value=int(it["amount"]),
                    step=1000,
                    key=f"amt_{i}"
                )

        with colB:
            st.markdown("### Vendor / Issuer")
            vendor_name = st.text_input("Vendor Name")
            previous = get_last_remittance(vendor_name)
            vendor_address = st.text_area("Vendor Address")

            st.markdown("### Remittance")
            bank = st.text_input("Bank", previous.get("bank", ""))
            account_name = st.text_input("Account name", previous.get("account_name", ""))
            account_no = st.text_input("Account no", previous.get("account_no", ""))
            swift = st.text_input("SWIFT Code", previous.get("swift", ""))

            template_choice = st.selectbox("Template", ["Cream Minimalist", "Playful Pastel", "Modern Monochrome"])
            save_pdf = st.checkbox("Save PDF", value=True)

        submit = st.form_submit_button("Generate Invoice")

    if submit:
        # Clean items
        items = [{
            "name": st.session_state[f"name_{i}"],
            "desc": st.session_state[f"desc_{i}"],
            "amount": float(st.session_state[f"amt_{i}"])
        } for i in range(len(st.session_state.line_items))]

        items = [i for i in items if i["name"]]

        total = sum(i["amount"] for i in items)

        vendor_initials = make_initials(vendor_name)
        inv_dt = datetime.combine(invoice_date, datetime.min.time())
        seq = get_next_sequence(vendor_initials, inv_dt.year)
        invoice_no = build_invoice_number(vendor_initials, seq, inv_dt)

        tpl_map = {
            "Cream Minimalist": "cream",
            "Playful Pastel": "pastel",
            "Modern Monochrome": "mono"
        }
        tpl_key = tpl_map[template_choice]

        data = {
            "invoice_no": invoice_no,
            "invoice_date": inv_dt,
            "due_date": datetime.combine(due_date, datetime.min.time()),
            "bill_to": name,
            "bill_address": bill_address,
            "items": items,
            "total": total,
            "currency_symbol": currency_symbol,
            "remittance": {
                "bank": bank,
                "account_name": account_name,
                "account_no": account_no,
                "swift": swift
            },
            "vendor_name": vendor_name,
            "vendor_address": vendor_address
        }

        pdf_bytes = create_pdf_bytes(data, tpl_key)
        filename = f"{invoice_no.replace('/', '-')}.pdf"

        bucket = supabase.storage.from_("invoices")

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        filename = f"{invoice_no.replace('/', '-')}_{timestamp}.pdf"
        
        # Upload PDF
        try:
            bucket.upload(
                path=filename,
                file=pdf_bytes,
                file_options={"content-type": "application/pdf"}
            )
            # Get public URL
            pdf_url = bucket.get_public_url(filename)["publicUrl"]
        
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
            st.write(f"**{invoice_no}** — {vendor_name}")
            st.write(f"Date: {invoice_date.strftime('%d-%b-%Y')}, Due: {due_date.strftime('%d-%b-%Y')}")
            st.write("Items:")
            for i, it in enumerate(items, 1):
                st.write(f"{i}. {it['name']} — {it.get('desc','')} — {currency_symbol} {int(it['amount']):,}")
            st.write(f"**TOTAL: {currency_symbol} {int(total):,}**")
            st.write("Remittance:")
            st.write(f"{bank} • {account_name} • {account_no} • SWIFT: {swift}")
    
        with colp2:
            st.download_button("Download PDF", data=pdf_bytes, file_name=filename, mime="application/pdf")

# -----------------------
# Footer
# -----------------------

st.markdown("""
---
<div style='text-align:center; color:#7c7368; font-size:13px;'>
<b>Paperbean</b> • v4.2.2 — A soft & tidy invoice & PO generator<br>
© 2025 Paperbean
</div>
""", unsafe_allow_html=True)
