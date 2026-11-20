import os
import sqlite3
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from config import DATABASE_URL
import calendar
from datetime import datetime, date
from dateutil.relativedelta import relativedelta

# ------------------------------ [Database Connection] ------------------------------
if DATABASE_URL:
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn.autocommit = True
    IS_POSTGRES = True
    SQL_PLACEHOLDER = "%s"
else:
    conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "local.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    IS_POSTGRES = False
    SQL_PLACEHOLDER = "?"

@contextmanager
def get_cursor():
    cur = conn.cursor()
    try:
        yield cur
    finally:
        cur.close()

# ------------------------------ [Initialize Tables] ------------------------------
def init_db():
    with get_cursor() as cur:
        if IS_POSTGRES:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS expense_record (
                    id SERIAL PRIMARY KEY,
                    date DATE NOT NULL,
                    description VARCHAR(200),
                    amount NUMERIC(10,2),
                    currency VARCHAR(3),
                    category VARCHAR(50),
                    forint NUMERIC(12,2)
                );
                CREATE TABLE IF NOT EXISTS income_record (
                    id SERIAL PRIMARY KEY,
                    date DATE NOT NULL,
                    description VARCHAR(200),
                    amount NUMERIC(10,2),
                    currency VARCHAR(3),
                    forint NUMERIC(12,2)
                );
            """)
        else:
            cur.execute("CREATE TABLE IF NOT EXISTS expense_record (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, description TEXT, amount REAL, currency TEXT, category TEXT, forint REAL);")
            cur.execute("CREATE TABLE IF NOT EXISTS income_record (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT, description TEXT, amount REAL, currency TEXT, forint REAL);")

def init_targets():
    with get_cursor() as cur:
        if IS_POSTGRES:
            cur.execute("CREATE TABLE IF NOT EXISTS expense_target (id SERIAL PRIMARY KEY, month CHAR(7), category VARCHAR(50), amount NUMERIC(12,2));")
        else:
            cur.execute("CREATE TABLE IF NOT EXISTS expense_target (id INTEGER PRIMARY KEY AUTOINCREMENT, month TEXT, category TEXT, amount REAL);")

def init_money_log():
    with get_cursor() as cur:
        if IS_POSTGRES:
            cur.execute("CREATE TABLE IF NOT EXISTS money_log (id SERIAL PRIMARY KEY, date DATE NOT NULL, platform VARCHAR(100), amount NUMERIC(12,2), currency CHAR(3) DEFAULT 'HUF', liquid INTEGER DEFAULT 0, eur_rate NUMERIC(12,4), usd_rate NUMERIC(12,4), forint NUMERIC(12,2));")
        else:
            cur.execute("CREATE TABLE IF NOT EXISTS money_log (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, platform TEXT, amount REAL, currency TEXT DEFAULT 'HUF', liquid INTEGER DEFAULT 0, eur_rate REAL, usd_rate REAL, forint REAL);")

# ------------------------------ [Initialize All Tables] ------------------------------
# The app uses exactly four tables: expense_record, income_record, expense_target, money_log
def init_all():
    init_db()
    init_targets()
    init_money_log()
    if not IS_POSTGRES:
        conn.commit()

# ------------------------------ [Core Logic Functions] ------------------------------
def get_categories_for_month(month):
    with get_cursor() as cur:
        cur.execute(f"SELECT * FROM expense_target WHERE month={SQL_PLACEHOLDER} ORDER BY amount DESC", (month,))
        return cur.fetchall()

def get_annual_targets(year):
    with get_cursor() as cur:
        cur.execute(f"SELECT month, category, amount FROM expense_target WHERE month LIKE {SQL_PLACEHOLDER} ORDER BY month ASC, amount DESC", (f"{year}-%",))
        return cur.fetchall()

def get_monthly_records(month_str, table_name):
    date_clause = "to_char(date, 'YYYY-MM') = %s" if IS_POSTGRES else "strftime('%Y-%m', date) = ?"
    with get_cursor() as cur:
        cur.execute(f"SELECT * FROM {table_name} WHERE {date_clause} ORDER BY date DESC", (month_str,))
        return cur.fetchall()

def get_annual_summary(year):
    date_clause = "to_char(date, 'YYYY-MM') = %s" if IS_POSTGRES else "strftime('%Y-%m', date) = ?"
    annual_data = []
    with get_cursor() as cur:
        for m in range(1, 13):
            month_str = f"{year}-{m:02d}"
            cur.execute(f"SELECT SUM(forint) AS total_val FROM income_record WHERE {date_clause} AND forint > 0", (month_str,))
            res_in = cur.fetchone()
            inflow = res_in["total_val"] if res_in and res_in["total_val"] else 0
            
            cur.execute(f"SELECT SUM(forint) AS total_val FROM income_record WHERE {date_clause} AND forint < 0", (month_str,))
            res_out = cur.fetchone()
            outflow = res_out["total_val"] if res_out and res_out["total_val"] else 0
            
            annual_data.append({
                "month_name": calendar.month_name[m],
                "inflow": float(inflow),
                "outflow": float(outflow),
                "net": float(inflow + outflow)
            })
    return annual_data

def get_summary(from_date, to_date):
    month_str = from_date[:7]
    date_clause = "to_char(date, 'YYYY-MM') = %s" if IS_POSTGRES else "strftime('%Y-%m', date) = ?"
    with get_cursor() as cur:
        cur.execute(f"SELECT category, SUM(amount) AS target FROM expense_target WHERE month = {SQL_PLACEHOLDER} GROUP BY category", (month_str,))
        targets = {r["category"]: float(r["target"]) for r in cur.fetchall()}
        cur.execute(f"SELECT category, SUM(forint) AS spent FROM expense_record WHERE {date_clause} GROUP BY category", (month_str,))
        spent = {r["category"]: float(r["spent"]) for r in cur.fetchall() if r["spent"]}
    
    rows = []
    t_t = t_s = t_r = 0.0
    all_categories = sorted(set(targets.keys()) | set(spent.keys()))
    for cat in all_categories:
        target, s = targets.get(cat, 0.0), spent.get(cat, 0.0)
        r = target - s
        rows.append({"category": cat, "target": round(target,2), "spent": round(s,2), "remaining": round(r,2)})
        t_t += target; t_s += s; t_r += r
    return {"rows": rows, "total": {"target": t_t, "spent": t_s, "remaining": t_r}}

def get_money_log_summary():
    with get_cursor() as cur:
        query = """
            SELECT date, SUM(forint) AS total_wealth_huf,
                SUM(CASE WHEN liquid = 1 THEN forint ELSE 0 END) AS total_liquid_huf,
                SUM(CASE WHEN currency = 'HUF' THEN amount ELSE 0 END) AS sum_huf,
                SUM(CASE WHEN currency = 'EUR' THEN amount ELSE 0 END) AS sum_eur,
                SUM(CASE WHEN currency = 'USD' THEN amount ELSE 0 END) AS sum_usd,
                AVG(eur_rate) AS avg_eur_rate, AVG(usd_rate) AS avg_usd_rate
            FROM money_log GROUP BY date ORDER BY date DESC
        """
        cur.execute(query)
        summary_rows = []
        for r in cur.fetchall():
            huf_total = float(r['total_wealth_huf'] or 0)
            eur_rate, usd_rate = float(r['avg_eur_rate'] or 1), float(r['avg_usd_rate'] or 1)
            summary_rows.append({
                "date": r['date'], "total_wealth_huf": huf_total,
                "total_wealth_eur": huf_total / eur_rate if eur_rate else 0,
                "total_liquid_huf": float(r['total_liquid_huf'] or 0),
                "sum_huf": float(r['sum_huf'] or 0), "sum_eur": float(r['sum_eur'] or 0), "sum_usd": float(r['sum_usd'] or 0),
                "p_huf": (float(r['sum_huf'] or 0) / huf_total * 100) if huf_total else 0,
                "p_eur": ((float(r['sum_eur'] or 0) * eur_rate) / huf_total * 100) if huf_total else 0,
                "p_usd": ((float(r['sum_usd'] or 0) * usd_rate) / huf_total * 100) if huf_total else 0,
                "eur_rate": eur_rate, "usd_rate": usd_rate
            })
        return summary_rows

def save_money_logs(selected_date, eur_rate, usd_rate, rows):
    formatted_date = selected_date.replace('.', '-')
    try:
        e_rate = float(eur_rate) if eur_rate else 1.0
        u_rate = float(usd_rate) if usd_rate else 1.0
    except (ValueError, TypeError):
        e_rate, u_rate = 1.0, 1.0

    with get_cursor() as cur:
        cur.execute(f"DELETE FROM money_log WHERE date = {SQL_PLACEHOLDER}", (formatted_date,))
        for row in rows:
            try:
                amt = float(row['amount']) if row['amount'] else 0.0
            except (ValueError, TypeError):
                amt = 0.0
                
            curr = row['currency']
            f_val = amt * (e_rate if curr == 'EUR' else u_rate if curr == 'USD' else 1.0)
            
            cur.execute(f"""
                INSERT INTO money_log 
                (date, platform, amount, currency, liquid, eur_rate, usd_rate, forint) 
                VALUES ({SQL_PLACEHOLDER},{SQL_PLACEHOLDER},{SQL_PLACEHOLDER},{SQL_PLACEHOLDER},{SQL_PLACEHOLDER},{SQL_PLACEHOLDER},{SQL_PLACEHOLDER},{SQL_PLACEHOLDER})
            """, (formatted_date, row['platform'], amt, curr, int(row['liquid']), e_rate, u_rate, f_val))
    return True

# ------------------------------ [CRUD Operations] ------------------------------
def month_start(d):
    """income_record is tracked per month only: any date becomes the 1st of its month (YYYY-MM-01)."""
    return str(d)[:7] + "-01"

def add_record(table, data):
    if table == "income":
        sql = f"INSERT INTO income_record (date, description, amount, currency, forint) VALUES ({SQL_PLACEHOLDER},{SQL_PLACEHOLDER},{SQL_PLACEHOLDER},{SQL_PLACEHOLDER},{SQL_PLACEHOLDER})"
        params = (month_start(data['date']), data['description'], data['amount'], data['currency'], data['forint'])
    else:
        sql = f"INSERT INTO expense_record (date, description, amount, currency, category, forint) VALUES ({SQL_PLACEHOLDER},{SQL_PLACEHOLDER},{SQL_PLACEHOLDER},{SQL_PLACEHOLDER},{SQL_PLACEHOLDER},{SQL_PLACEHOLDER})"
        params = (data['date'], data['description'], data['amount'], data['currency'], data['category'], data['forint'])
    with get_cursor() as cur:
        cur.execute(sql, params)

def update_record(table, record_id, data):
    with get_cursor() as cur:
        if table == "income":
            cur.execute(f"UPDATE income_record SET date={SQL_PLACEHOLDER}, description={SQL_PLACEHOLDER}, amount={SQL_PLACEHOLDER}, currency={SQL_PLACEHOLDER}, forint={SQL_PLACEHOLDER} WHERE id={SQL_PLACEHOLDER}", 
                        (month_start(data['date']), data['description'], data['amount'], data['currency'], data['forint'], record_id))
        else:
            cur.execute(f"UPDATE expense_record SET date={SQL_PLACEHOLDER}, description={SQL_PLACEHOLDER}, amount={SQL_PLACEHOLDER}, currency={SQL_PLACEHOLDER}, category={SQL_PLACEHOLDER}, forint={SQL_PLACEHOLDER} WHERE id={SQL_PLACEHOLDER}", 
                        (data['date'], data['description'], data['amount'], data['currency'], data['category'], data['forint'], record_id))

def delete_record(table, record_id):
    with get_cursor() as cur:
        cur.execute(f"DELETE FROM {table}_record WHERE id={SQL_PLACEHOLDER}", (record_id,))

def get_single_record(table, record_id):
    with get_cursor() as cur:
        cur.execute(f"SELECT * FROM {table}_record WHERE id={SQL_PLACEHOLDER}", (record_id,))
        return cur.fetchone()

def generate_monthly_cashflows(target_month_str):
    target_date = datetime.strptime(target_month_str, "%Y-%m")
    prev_date = target_date - relativedelta(months=1)
    prev_month_str = prev_date.strftime("%Y-%m")
    first_day_target = target_date.strftime("%Y-%m-01")
    date_clause = "to_char(date, 'YYYY-MM') = %s" if IS_POSTGRES else "strftime('%Y-%m', date) = ?"

    with get_cursor() as cur:
        cur.execute(f"DELETE FROM income_record WHERE {date_clause}", (target_month_str,))
        cur.execute(f"SELECT SUM(amount) as total_target FROM expense_target WHERE month = {SQL_PLACEHOLDER}", (prev_month_str,))
        target_res = cur.fetchone()
        target_sum = float(target_res["total_target"]) if target_res and target_res["total_target"] else 0.0
        
        if target_sum > 0:
            cur.execute(f"""
                INSERT INTO income_record (date, description, amount, currency, forint)
                VALUES ({SQL_PLACEHOLDER}, {SQL_PLACEHOLDER}, {SQL_PLACEHOLDER}, {SQL_PLACEHOLDER}, {SQL_PLACEHOLDER})
            """, (first_day_target, "Target Budget Allocation", -target_sum, "HUF", -target_sum))

        cur.execute(f"""
            SELECT date, description, amount, currency, forint
            FROM income_record
            WHERE {date_clause} AND description != 'Target Budget Allocation'
        """, (prev_month_str,))
        
        prev_records = cur.fetchall()
        for rec in prev_records:
            new_date = first_day_target
            cur.execute(f"""
                INSERT INTO income_record (date, description, amount, currency, forint)
                VALUES ({SQL_PLACEHOLDER}, {SQL_PLACEHOLDER}, {SQL_PLACEHOLDER}, {SQL_PLACEHOLDER}, {SQL_PLACEHOLDER})
            """, (new_date, rec["description"], rec["amount"], rec["currency"], rec["forint"]))