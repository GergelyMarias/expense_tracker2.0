import pickle
import io
import datetime
from db import conn, get_cursor, IS_POSTGRES, SQL_PLACEHOLDER

# The only tables this app uses; backup, restore and info all work from this list
TABLES = ['expense_record', 'income_record', 'expense_target', 'money_log', 'portfolio_tx', 'portfolio_price']

# ----------------------------- [GENERATE BACKUP DATA] -----------------------------
def get_backup_bytes():
    tables = TABLES
    backup_data = {}
    status_report = []

    with get_cursor() as cur:
        for table in tables:
            try:
                cur.execute(f"SELECT * FROM {table}")
                rows = cur.fetchall()
                backup_data[table] = [dict(row) for row in rows]
                status_report.append({"table": table, "status": "✅ Exported", "rows": len(rows)})
            except Exception as e:
                status_report.append({"table": table, "status": f"❌ Error: {str(e)}", "rows": 0})
    
    # Convert dictionary to bytes in memory
    buffer = io.BytesIO()
    pickle.dump(backup_data, buffer)
    buffer.seek(0)
    
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"finance_backup_{timestamp}.pkl"
    
    return buffer, filename, status_report

# ----------------------------- [PROCESS UPLOADED BACKUP] -----------------------------
def restore_from_bytes(file_bytes):
    status_report = []
    try:
        backup_data = pickle.loads(file_bytes)
        
        with get_cursor() as cur:
            for table, rows in backup_data.items():
                # Old backups may contain tables the app no longer uses: skip them
                if table not in TABLES:
                    status_report.append({"table": table, "status": "⏭️ Skipped (not used by the app)", "rows": len(rows)})
                    continue
                try:
                    cur.execute(f"DELETE FROM {table}")
                    if rows:
                        columns = rows[0].keys()
                        col_names = ", ".join(columns)
                        placeholders = ", ".join([SQL_PLACEHOLDER] * len(columns))
                        query = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"
                        
                        for row in rows:
                            cur.execute(query, tuple(row[col] for col in columns))
                    
                    if IS_POSTGRES:
                        cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), COALESCE(MAX(id), 1), false) FROM {table}")
                    
                    status_report.append({"table": table, "status": "✅ Restored", "rows": len(rows)})
                except Exception as table_err:
                    status_report.append({"table": table, "status": f"❌ Failed: {str(table_err)}", "rows": 0})

        conn.commit()
        return True, status_report
    except Exception as e:
        conn.rollback()
        return False, [{"table": "FILE", "status": f"❌ Invalid Backup File: {str(e)}", "rows": 0}]

# ----------------------------- [DATABASE INFO (DETAILED)] -----------------------------
def get_db_info():
    tables = TABLES
    stats = []
    with get_cursor() as cur:
        for table in tables:
            # Get Row Count
            cur.execute(f"SELECT COUNT(*) as cnt FROM {table}")
            res = cur.fetchone()
            count = res['cnt'] if res else 0
            
            # Get Column Names based on DB engine
            if IS_POSTGRES:
                cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name='{table}'")
                cols = [r['column_name'] for r in cur.fetchall()]
            else:
                cur.execute(f"PRAGMA table_info({table})")
                cols = [r[1] for r in cur.fetchall()]
                
            stats.append({
                "name": table, 
                "rows": count, 
                "cols": ", ".join(cols)
            })
    return stats