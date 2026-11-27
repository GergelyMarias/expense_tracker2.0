from flask import Blueprint, render_template, request, redirect, session, flash
from datetime import date
import db
from utils import convert_to_huf

expenses_bp = Blueprint("expenses", __name__)

# ------------------------------ Expenses Route ------------------------------
# Old dashboard addresses: forward to the dashboard (keeps month/year)
@expenses_bp.route("/expenses")
@expenses_bp.route("/dashboard-b")
def expenses():
    qs = request.query_string.decode()
    return redirect("/dashboard" + (f"?{qs}" if qs else ""))


@expenses_bp.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    return _dashboard("dashboard.html", "/dashboard")


def _dashboard(template, base_url):
    if not session.get("logged_in"):
        return redirect("/")

    selected_month = request.args.get("month") or date.today().strftime("%Y-%m")
    
    # Automatically sync the year to the selected month variable if query parameter is empty
    selected_year = request.args.get("year") or selected_month.split("-")[0]

    if request.method == "POST":
        d = request.form.get("date")
        amt = float(request.form.get("amount"))
        cur = request.form.get("currency")
        desc = request.form.get("description", "")[:200]
        try:
            huf_val = convert_to_huf(amt, cur, d)
        except Exception as e:  # FX lookup failed (Yahoo down / blocked / no data)
            flash(f"Expense not saved: could not convert {cur} to HUF ({e}). Try again or enter it in HUF.")
            return redirect(f"{base_url}?month={selected_month}&year={selected_year}")
        
        form_type = request.form.get("form_type")
        data = {"date": d, "description": desc, "amount": amt, "currency": cur, "forint": huf_val}
        
        if form_type != "income":
            data["category"] = request.form.get("category")
        
        db.add_record(form_type, data)
        return redirect(f"{base_url}?month={selected_month}&year={selected_year}")

    return render_template(
        template,
        expenses=db.get_monthly_records(selected_month, "expense_record"),
        summary=db.get_summary(f"{selected_month}-01", f"{selected_month}-31"),
        selected_year=selected_year,
        today=date.today().isoformat(),
        exp_month=selected_month,
    )

# ------------------------------ Expense Management ------------------------------
def _back_url():
    """Where to return after editing/deleting: the dashboard you came from, same month."""
    back = "/dashboard"
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    year = request.args.get("year", month.split("-")[0])
    return f"{back}?month={month}&year={year}", back, month, year


@expenses_bp.route("/expense/edit/<int:id>", methods=["GET", "POST"])
def edit_expense(id):
    if not session.get("logged_in"): return redirect("/")
    back_url, back, month, year = _back_url()

    if request.method == "POST":
        d, amt, cur = request.form.get("date"), float(request.form.get("amount")), request.form.get("currency")
        try:
            huf_val = convert_to_huf(amt, cur, d)
        except Exception as e:
            flash(f"Expense not updated: could not convert {cur} to HUF ({e}). Try again or enter it in HUF.")
            return redirect(back_url)
        data = {"date": d, "description": request.form.get("description", "")[:200], "amount": amt, "currency": cur,
                "category": request.form.get("category"), "forint": huf_val}
        db.update_record("expense", id, data)
        return redirect(back_url)

    row = db.get_single_record("expense", id)
    if not row:
        flash("That expense no longer exists.")
        return redirect(back_url)
    exp = dict(row)
    exp['date'] = str(exp['date'])[:10]
    return render_template("edit.html", expense=exp, month=month, year=year, back=back, back_url=back_url)

@expenses_bp.route("/expense/delete/<int:id>", methods=["POST"])
def delete_expense(id):
    if session.get("logged_in"): db.delete_record("expense", id)
    return redirect(_back_url()[0])
