"""Shared "month grid" page: rows = a label (category / description), columns = 12 months.

Used by Expense Targets (expense_target) and CashFlow (income_record). Each page gives a
GridStore describing how its table is read and written; routes and template are shared.
"""
from dataclasses import dataclass
from datetime import date
import calendar

from flask import Blueprint, render_template, request, redirect, session, jsonify

from db import get_cursor, SQL_PLACEHOLDER as P, IS_POSTGRES


@dataclass
class GridStore:
    table: str          # SQL table
    label_col: str      # row label column (category / description)
    month_col: str      # column holding the period
    month_is_text: bool  # True: 'YYYY-MM' text column; False: a date stored as YYYY-MM-01

    # SQL expression giving 'YYYY-MM' for a row
    def month_expr(self):
        if self.month_is_text or not IS_POSTGRES:
            return f"substr({self.month_col}, 1, 7)"
        return f"to_char({self.month_col}, 'YYYY-MM')"

    def rows_for_year(self, year):
        """[(month 1..12, label, amount)] for one year."""
        m = self.month_expr()
        with get_cursor() as c:
            c.execute(
                f"SELECT {m} AS m, {self.label_col} AS label, amount FROM {self.table} WHERE {m} LIKE {P}",
                (f"{year}-%",),
            )
            return [(int(r["m"][5:7]), r["label"], float(r["amount"] or 0)) for r in c.fetchall()]

    def year_totals(self):
        """[(year, label, total)] across all years."""
        m = self.month_expr()
        with get_cursor() as c:
            c.execute(f"""
                SELECT substr({m}, 1, 4) AS y, {self.label_col} AS label, SUM(amount) AS total
                FROM {self.table} GROUP BY substr({m}, 1, 4), {self.label_col}
            """)
            return [(int(r["y"]), r["label"], float(r["total"] or 0)) for r in c.fetchall()]

    def years(self):
        m = self.month_expr()
        with get_cursor() as c:
            c.execute(f"SELECT DISTINCT substr({m}, 1, 4) AS y FROM {self.table}")
            return {int(r["y"]) for r in c.fetchall() if r["y"]}

    def replace_cell(self, cur, year, month, label, amount):
        """Delete the cell's existing rows, then insert the new value (None = leave empty)."""
        period = f"{year}-{month:02d}"
        cur.execute(
            f"DELETE FROM {self.table} WHERE {self.month_expr()} = {P} AND {self.label_col} = {P}",
            (period, label),
        )
        if amount is None:
            return
        if self.table == "expense_target":
            cur.execute(
                f"INSERT INTO expense_target (month, category, amount) VALUES ({P},{P},{P})",
                (period, label, amount),
            )
        else:  # income_record: monthly only, always HUF
            cur.execute(
                f"INSERT INTO income_record (date, description, amount, currency, forint) VALUES ({P},{P},{P},{P},{P})",
                (f"{period}-01", label, amount, "HUF", amount),
            )


def _ordered(matrix):
    """Biggest total first; ties alphabetically."""
    return sorted(matrix, key=lambda k: (-sum(matrix[k].values()), k.lower()))


def make_grid_blueprint(name, page_url, api_url, store, page):
    """page: dict of display settings passed to the template (title, labels, allow_negative...)."""
    bp = Blueprint(name, __name__)

    def year_matrix(year):
        matrix = {}
        for m, label, amount in store.rows_for_year(year):
            row = matrix.setdefault(label, {})
            row[m] = row.get(m, 0) + amount
        return matrix

    @bp.route(page_url)
    def grid_page():
        if not session.get("logged_in"):
            return redirect("/")
        try:
            year = int(request.args.get("year") or date.today().year)
        except ValueError:
            year = date.today().year

        matrix = year_matrix(year)
        recorded = store.years()
        return render_template(
            "month_grid.html",
            page=page,
            api_url=api_url,
            page_url=page_url,
            year=year,
            years=list(range(min(recorded | {date.today().year}), 2101)),
            months=[(i, calendar.month_name[i]) for i in range(1, 13)],
            labels=_ordered(matrix),
            matrix=matrix,
            prev_year_has_data=not matrix and (year - 1) in recorded,
            view="annual" if request.args.get("view") == "annual" else "monthly",
        )

    @bp.route(f"{api_url}/annual", endpoint="annual")
    def annual():
        if not session.get("logged_in"):
            return jsonify(error="unauthorized"), 401
        totals = {}
        for y, label, total in store.year_totals():
            totals.setdefault(label, {})[y] = total
        years = sorted({y for t in totals.values() for y in t})
        # Colour order: biggest absolute all-years total first
        ordered = sorted(totals, key=lambda k: (-sum(abs(v) for v in totals[k].values()), k.lower()))
        return jsonify(years=years, categories=[{"category": k, "totals": totals[k]} for k in ordered])

    @bp.route(f"{api_url}/<int:year>", endpoint="read_year")
    def read_year(year):
        """Read-only: pre-fills the table for 'Copy from previous year' (saved only on Save)."""
        if not session.get("logged_in"):
            return jsonify(error="unauthorized"), 401
        matrix = year_matrix(year)
        return jsonify(categories=[{"category": k, "months": matrix[k]} for k in _ordered(matrix)])

    @bp.route(api_url, methods=["POST"], endpoint="save")
    def save():
        """Body: {"year": 2026, "changes": [{"month": 1-12, "category": str, "amount": num | ""}]}"""
        if not session.get("logged_in"):
            return jsonify(error="unauthorized"), 401
        data = request.get_json(silent=True) or {}
        parsed = []
        try:
            year = int(data["year"])
            for ch in data["changes"]:
                month = int(ch["month"])
                label = str(ch["category"]).strip()
                amount = ch.get("amount")
                amount = None if amount in (None, "", "-") else float(amount)
                if not label or not 1 <= month <= 12:
                    raise ValueError
                if amount is not None and amount < 0 and not page.get("allow_negative"):
                    raise ValueError
                parsed.append((month, label, amount))
        except (KeyError, TypeError, ValueError):
            return jsonify(error="invalid input"), 400

        with get_cursor() as c:
            for month, label, amount in parsed:
                store.replace_cell(c, year, month, label, amount)
            c.connection.commit()
        return jsonify(ok=True, saved=len(parsed))

    return bp
