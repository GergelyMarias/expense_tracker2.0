from flask import Flask, session, redirect, url_for, request, render_template, send_file
import os
import sys

# Entry point lives at the project root (so `gunicorn app:app` works on Render);
# the rest of the backend code is in backend/, so make it importable.
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "backend"))

# ------------------------------ [Imports & Config] ------------------------------
from db import init_all
from routes import register_routes
from config import SECRET_KEY
from utils import format_amount, huf
from backup_utils import get_db_info, get_backup_bytes, restore_from_bytes

app = Flask(__name__, template_folder=os.path.join(ROOT, "frontend"))
app.secret_key = SECRET_KEY

# Register Jinja filters
app.jinja_env.filters["amount"] = format_amount  
app.jinja_env.filters["huf"] = huf

# ------------------------------ [Database Initialization] ------------------------------
init_all()

# ------------------------------ [Security Middleware] ------------------------------
@app.before_request
def require_login():
    allowed_endpoints = ['auth.login', 'static']
    if request.endpoint not in allowed_endpoints and not session.get('logged_in'):
        return redirect(url_for('auth.login'))

# ------------------------------ [Privacy: hide amounts] ------------------------------
@app.context_processor
def inject_privacy():
    # Every template gets `hide_amounts`; templates/_privacy.html blurs money values when it's on
    return {"hide_amounts": bool(session.get("hide_amounts"))}

@app.route('/settings/privacy', methods=['POST'])
def set_privacy():
    session['hide_amounts'] = request.form.get('hide_amounts') == 'on'
    nxt = request.form.get('next') or ''
    # only redirect to local pages
    return redirect(nxt if nxt.startswith('/') and not nxt.startswith('//') else url_for('settings_page'))

# ------------------------------ [Settings (backup, restore, database info)] ------------------------------
@app.route('/settings')
def settings_page():
    from db import IS_POSTGRES
    try:
        info = get_db_info()
    except Exception as e:  # never let a stats query break the page
        info, _ = [], e
    return render_template(
        'settings.html',
        info=info,
        engine="PostgreSQL" if IS_POSTGRES else "SQLite (local.db)",
        last_status=session.pop('backup_status', None),
        last_ok=session.pop('backup_ok', None),
    )

# Old address: keep bookmarks working
@app.route('/database')
def database_page():
    return redirect(url_for('settings_page'))

@app.route('/db/download-backup')
def download_backup():
    buffer, filename, status = get_backup_bytes()
    return send_file(
        buffer, 
        as_attachment=True, 
        download_name=filename, 
        mimetype='application/octet-stream'
    )

@app.route('/db/upload-restore', methods=['POST'])
def upload_restore():
    if 'backup_file' not in request.files:
        return redirect(url_for('settings_page'))
        
    file = request.files['backup_file']
    if file.filename == '':
        return redirect(url_for('settings_page'))

    success, status = restore_from_bytes(file.read())
    session['backup_status'] = status
    session['backup_ok'] = success
    return redirect(url_for('settings_page'))

# ------------------------------ [Main Routing Registration] ------------------------------

# Register standard routes and the new Investment Blueprint
register_routes(app)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)