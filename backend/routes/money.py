from flask import Blueprint, render_template, request, jsonify, redirect
from db import get_cursor, save_money_logs, SQL_PLACEHOLDER, IS_POSTGRES
from datetime import datetime
from utils import get_huf_rate

money_bp = Blueprint('money', __name__)

# ------------------------------ [Old Money page address] ------------------------------
@money_bp.route('/money')
def money_summary():
    return redirect('/money-b')

# ------------------------------ [Money Register View] ------------------------------
@money_bp.route('/money/register')
def register_money():
    with get_cursor() as cur:
        cur.execute("SELECT DISTINCT date FROM money_log ORDER BY date")
        snapshot_dates = [str(r["date"])[:10] for r in cur.fetchall()]
    return render_template('register_money.html', snapshot_dates=snapshot_dates)


# ------------------------------ [API: FX rates for a date (Yahoo Finance)] ------------------------------
@money_bp.route('/api/fx-rates/<date_str>')
def api_fx_rates(date_str):
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return jsonify(error="invalid date"), 400
    try:
        return jsonify(eur=get_huf_rate("EUR", date_str), usd=get_huf_rate("USD", date_str), source="Yahoo Finance")
    except Exception as e:
        return jsonify(error=str(e)), 502

# ------------------------------ [API: Fetch Logs by Date] ------------------------------
@money_bp.route('/api/money-logs/<date_str>')
def api_get_logs(date_str):
    formatted_date = date_str.replace('.', '-')
    with get_cursor() as cur:
        cur.execute(f"SELECT platform, amount, currency, liquid, eur_rate, usd_rate FROM money_log WHERE date = {SQL_PLACEHOLDER}", (formatted_date,))
        logs = cur.fetchall()
    return jsonify([dict(row) for row in logs])

# ------------------------------ [API: Fetch Latest Entry Structure] ------------------------------
@money_bp.route('/api/money-logs/latest')
def api_get_latest_logs():
    with get_cursor() as cur:
        cur.execute("SELECT MAX(date) as last_date FROM money_log")
        row = cur.fetchone()
        
        if row:
            if isinstance(row, dict) or hasattr(row, 'keys'):
                last_date = row['last_date']
            else:
                last_date = row[0]
        else:
            last_date = None
        
        if last_date:
            cur.execute(f"SELECT platform, amount, currency, liquid FROM money_log WHERE date = {SQL_PLACEHOLDER}", (last_date,))
            logs = cur.fetchall()
            return jsonify([dict(row) for row in logs])
    return jsonify([])

# ------------------------------ [API: Save/Update Logs] ------------------------------
@money_bp.route('/api/money-logs', methods=['POST'])
def api_save_logs():
    data = request.get_json(silent=True)
    if not data or 'date' not in data:
        return jsonify({"status": "error", "message": "Invalid data"}), 400

    # Rates come from Yahoo Finance for the snapshot date; manual values are only a fallback.
    # keep_rates: re-saving an existing snapshot with the rates it was recorded with.
    try:
        if data.get('keep_rates') and data.get('eur_rate') and data.get('usd_rate'):
            eur_rate, usd_rate = float(data['eur_rate']), float(data['usd_rate'])
        else:
            eur_rate = get_huf_rate("EUR", data['date'])
            usd_rate = get_huf_rate("USD", data['date'])
    except Exception as e:
        eur_rate, usd_rate = data.get('eur_rate'), data.get('usd_rate')
        uses_fx = any(r.get('currency') in ('EUR', 'USD') for r in data.get('rows', []))
        if uses_fx and not (eur_rate and usd_rate):
            return jsonify({"status": "error", "message": f"Exchange rates unavailable ({e}). Enter them manually."}), 502

    success = save_money_logs(
        data['date'],
        eur_rate,
        usd_rate,
        data['rows']
    )
    return jsonify({"status": "success" if success else "error"})

# ------------------------------ [Money B: redesigned overview] ------------------------------
def _num(v):
    return float(v or 0)


@money_bp.route('/money-b')
def money_b():
    """Net-worth overview: snapshots from money_log, plan from income_record, spend from expense_record."""
    month_extract = "to_char(date, 'YYYY-MM')" if IS_POSTGRES else "strftime('%Y-%m', date)"
    with get_cursor() as cur:
        # One row per snapshot date: totals, liquid part, and HUF value per currency
        cur.execute("""
            SELECT date,
                   SUM(forint) AS total,
                   SUM(CASE WHEN liquid = 1 THEN forint ELSE 0 END) AS liquid,
                   SUM(CASE WHEN currency = 'HUF' THEN forint ELSE 0 END) AS huf,
                   SUM(CASE WHEN currency = 'EUR' THEN forint ELSE 0 END) AS eur,
                   SUM(CASE WHEN currency = 'USD' THEN forint ELSE 0 END) AS usd,
                   AVG(eur_rate) AS eur_rate, AVG(usd_rate) AS usd_rate,
                   SUM(CASE WHEN currency = 'EUR' THEN amount ELSE 0 END) AS eur_amount,
                   SUM(CASE WHEN currency = 'USD' THEN amount ELSE 0 END) AS usd_amount
            FROM money_log GROUP BY date ORDER BY date ASC
        """)
        snaps = [{
            "date": str(r["date"])[:10],
            "total": _num(r["total"]), "liquid": _num(r["liquid"]),
            "huf": _num(r["huf"]), "eur": _num(r["eur"]), "usd": _num(r["usd"]),
            "eur_rate": _num(r["eur_rate"]), "usd_rate": _num(r["usd_rate"]),
            "eur_amount": _num(r["eur_amount"]), "usd_amount": _num(r["usd_amount"]),
        } for r in cur.fetchall()]

        # Per-account rows for the latest two snapshots (for breakdown + "what changed")
        accounts, prev_accounts = [], []
        if snaps:
            def accounts_on(d):
                cur.execute(f"""
                    SELECT platform, currency, liquid, amount, forint
                    FROM money_log WHERE date = {SQL_PLACEHOLDER} ORDER BY forint DESC
                """, (d,))
                return [{"platform": (r["platform"] or "").strip(), "currency": r["currency"],
                         "liquid": int(r["liquid"] or 0), "amount": _num(r["amount"]), "forint": _num(r["forint"])}
                        for r in cur.fetchall()]
            accounts = accounts_on(snaps[-1]["date"])
            if len(snaps) > 1:
                prev_accounts = accounts_on(snaps[-2]["date"])

        # Planned monthly net cash flow (CashFlow page) for the projection and plan-vs-actual
        cur.execute(f"""
            SELECT {month_extract} AS m, SUM(forint) AS net FROM income_record
            GROUP BY {month_extract} ORDER BY m ASC
        """)
        plan = [{"month": r["m"], "net": _num(r["net"])} for r in cur.fetchall() if r["m"]]

        # Actual monthly spending (expense_record) for the liquidity runway
        cur.execute(f"""
            SELECT {month_extract} AS m, SUM(forint) AS spent FROM expense_record
            GROUP BY {month_extract} ORDER BY m ASC
        """)
        spending = [{"month": r["m"], "spent": _num(r["spent"])} for r in cur.fetchall() if r["m"]]

    return render_template(
        'money_b.html',
        snaps=snaps, accounts=accounts, prev_accounts=prev_accounts,
        plan=plan, spending=spending,
        today=datetime.now().strftime("%Y-%m"),
    )
