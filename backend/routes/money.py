from flask import Blueprint, render_template, request, jsonify
from db import get_money_log_summary, get_cursor, save_money_logs, SQL_PLACEHOLDER, IS_POSTGRES
from datetime import datetime

money_bp = Blueprint('money', __name__)

# ------------------------------ [Money Summary View] ------------------------------
@money_bp.route('/money')
def money_summary():
    data = get_money_log_summary()
    
    # SQL segment extractor syntax adjusted automatically for Postgres vs SQLite engines
    month_extract = "to_char(date, 'YYYY-MM')" if IS_POSTGRES else "strftime('%Y-%m', date)"
    
    # ------------------------------ [Fetch Complete Net Income Sums] ------------------------------
    with get_cursor() as cur:
        cur.execute(f"""
            SELECT {month_extract} as income_month, SUM(forint) as total_forint
            FROM income_record
            GROUP BY income_month
            ORDER BY income_month ASC
        """)
        income_rows = cur.fetchall()
        
    month_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    
    # Extract the net bar chart parameters directly from the income rows
    bar_labels = []
    bar_values = []
    income_by_month = {}
    
    for row in income_rows:
        if isinstance(row, dict) or hasattr(row, 'keys'):
            m_str = row['income_month']
            val = float(row['total_forint'] or 0)
        else:
            m_str = row[0]
            val = float(row[1] or 0)
        
        try:
            p_date = datetime.strptime(m_str, "%Y-%m")
            display_month = f"{month_names[p_date.month - 1]} {p_date.year}"
            bar_labels.append(display_month)
            bar_values.append(val)
            income_by_month[m_str] = val
        except:
            continue

    # ------------------------------ [Generate Integrated Curve Data] ------------------------------
    curve_labels = []
    actual_wealth_series = []
    projected_wealth_series = []
    
    # Reverse summary data array to parse timeline in chronological order
    chronological_summary = list(reversed(data))
    
    last_actual_month_str = None
    running_wealth_baseline = 0.0
    
    # 1. Populate verified historical landmarks using money_log elements
    for entry in chronological_summary:
        raw_date = entry['date']
        try:
            if hasattr(raw_date, 'strftime'):
                p_date = raw_date
            else:
                p_date = datetime.strptime(str(raw_date), "%Y-%m-%d")
                
            m_key = p_date.strftime("%Y-%m")
            display_month = f"{month_names[p_date.month - 1]} {p_date.year}"
            
            running_wealth_baseline = float(entry['total_wealth_huf'] or 0)
            
            curve_labels.append(display_month)
            actual_wealth_series.append(running_wealth_baseline)
            projected_wealth_series.append(None) # Keeps projection blank during actual data span
            last_actual_month_str = m_key
        except:
            continue

    # 2. Append forward projections for periods beyond the logs using income_record adjustments
    if last_actual_month_str and actual_wealth_series:
        # Seed the first point of the projection dataset to smoothly bridge the lines
        projected_wealth_series[-1] = running_wealth_baseline
        
        for row in income_rows:
            if isinstance(row, dict) or hasattr(row, 'keys'):
                m_str = row['income_month']
                val = float(row['total_forint'] or 0)
            else:
                m_str = row[0]
                val = float(row[1] or 0)
            
            # Identify if this row's timeline occurs safely after the verified baseline anchor
            if m_str > last_actual_month_str:
                try:
                    p_date = datetime.strptime(m_str, "%Y-%m")
                    display_month = f"{month_names[p_date.month - 1]} {p_date.year}"
                    
                    running_wealth_baseline += val
                    
                    curve_labels.append(display_month)
                    actual_wealth_series.append(None)
                    projected_wealth_series.append(running_wealth_baseline)
                except:
                    continue
            
    return render_template(
        'money.html', 
        summary=data, 
        chart_labels=bar_labels, 
        chart_values=bar_values,
        curve_labels=curve_labels,
        actual_wealth=actual_wealth_series,
        projected_wealth=projected_wealth_series
    )

# ------------------------------ [Money Register View] ------------------------------
@money_bp.route('/money/register')
def register_money():
    return render_template('register_money.html')

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
    data = request.json
    if not data or 'date' not in data:
        return jsonify({"status": "error", "message": "Invalid data"}), 400
        
    success = save_money_logs(
        data['date'], 
        data['eur_rate'], 
        data['usd_rate'], 
        data['rows']
    )
    return jsonify({"status": "success" if success else "error"})