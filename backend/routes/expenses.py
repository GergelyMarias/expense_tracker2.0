from flask import Blueprint, render_template, request, redirect, session
from datetime import date
import db
from utils import convert_to_huf

expenses_bp = Blueprint("expenses", __name__)

# ------------------------------ Expenses Route ------------------------------
@expenses_bp.route("/expenses", methods=["GET", "POST"])
def expenses():
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
        huf_val = convert_to_huf(amt, cur, d)
        
        form_type = request.form.get("form_type")
        data = {"date": d, "description": desc, "amount": amt, "currency": cur, "forint": huf_val}
        
        if form_type != "income":
            data["category"] = request.form.get("category")
        
        db.add_record(form_type, data)
        return redirect(f"/expenses?month={selected_month}&year={selected_year}")

    return render_template(
        "main.html",
        expenses=db.get_monthly_records(selected_month, "expense_record"),
        summary=db.get_summary(f"{selected_month}-01", f"{selected_month}-31"),
        selected_year=selected_year,
        today=date.today().isoformat(),
        exp_month=selected_month,
    )

# ------------------------------ Income Management ------------------------------
@expenses_bp.route("/income/edit/<int:id>", methods=["GET", "POST"])
def edit_income(id):
    if not session.get("logged_in"): return redirect("/")
    
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    year = request.args.get("year", month.split("-")[0])
    
    print(f"DEBUG: Editing income {id} for month: {month}, year: {year}")
    
    if request.method == "POST":
        d, amt, cur = request.form.get("date"), float(request.form.get("amount")), request.form.get("currency")
        data = {"date": d, "description": request.form.get("description", "")[:200], "amount": amt, "currency": cur, "forint": convert_to_huf(amt, cur, d)}
        db.update_record("income", id, data)
        return redirect(f"/expenses?month={month}&year={year}")
        
    return render_template("edit_income.html", income=db.get_single_record("income", id), month=month, year=year)

@expenses_bp.route("/income/delete/<int:id>", methods=["POST"])
def delete_income(id):
    if session.get("logged_in"): 
        db.delete_record("income", id)
        
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    year = request.args.get("year", month.split("-")[0])
    return redirect(f"/expenses?month={month}&year={year}")

# ------------------------------ Expense Management ------------------------------
@expenses_bp.route("/expense/edit/<int:id>", methods=["GET", "POST"])
def edit_expense(id):
    if not session.get("logged_in"): return redirect("/")
    
    month = request.args.get("month", date.today().strftime("%Y-%m"))
    year = request.args.get("year", month.split("-")[0])
    
    if request.method == "POST":
        d, amt, cur = request.form.get("date"), float(request.form.get("amount")), request.form.get("currency")
        data = {"date": d, "description": request.form.get("description"), "amount": amt, "currency": cur, "category": request.form.get("category"), "forint": convert_to_huf(amt, cur, d)}
        db.update_record("expense", id, data)
        return redirect(f"/expenses?month={month}&year={year}")
    
    exp = dict(db.get_single_record("expense", id))
    if hasattr(exp['date'], 'isoformat'): exp['date'] = exp['date'].isoformat()
    return render_template("edit.html", expense=exp, month=month, year=year)

@expenses_bp.route("/expense/delete/<int:id>", methods=["POST"])
def delete_expense(id):
    if session.get("logged_in"): db.delete_record("expense", id)
    return redirect("/expenses")

# ------------------------------ Cashflow Automation ------------------------------
@expenses_bp.route("/expenses/generate", methods=["POST"])
def generate_cashflows():
    if not session.get("logged_in"):
        return redirect("/")
    
    current_month = request.form.get("month")
    
    if not current_month:
        return redirect("/expenses")

    db.generate_monthly_cashflows(current_month)
    return redirect(f"/expenses?month={current_month}&year={current_month.split('-')[0]}")