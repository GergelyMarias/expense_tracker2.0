from datetime import date

from routes.month_grid import GridStore, make_grid_blueprint
from db import get_cursor, SQL_PLACEHOLDER as P

# Row whose future months always mirror the Expense Targets total (stored as a negative outflow).
# Current and past months are normal, editable values.
ALLOCATION_LABEL = "Target Budget Allocation"


def _current_period():
    return date.today().strftime("%Y-%m")


def sync_target_allocation(cur=None):
    """Rewrite the allocation row for every month after the current one from expense_target."""
    def run(c):
        now = _current_period()
        c.execute(
            f"DELETE FROM income_record WHERE description = {P} AND substr(CAST(date AS TEXT), 1, 7) > {P}",
            (ALLOCATION_LABEL, now),
        )
        c.execute(
            f"SELECT month, SUM(amount) AS total FROM expense_target WHERE month > {P} GROUP BY month",
            (now,),
        )
        for r in c.fetchall():
            total = float(r["total"] or 0)
            if total:
                c.execute(
                    f"INSERT INTO income_record (date, description, amount, currency, forint) VALUES ({P},{P},{P},{P},{P})",
                    (f"{r['month']}-01", ALLOCATION_LABEL, -total, "HUF", -total),
                )
    if cur is not None:
        return run(cur)
    with get_cursor() as c:
        run(c)
        c.connection.commit()


def locked_months(year):
    """{label: {months}} that cannot be edited on the CashFlow grid for this year."""
    now = _current_period()
    return {ALLOCATION_LABEL: {m for m in range(1, 13) if f"{year}-{m:02d}" > now}}


# CashFlow: rows = income_record.description, one HUF value per description per month.
# Dates are stored as the first day of the month (YYYY-MM-01); negative values are outflows.
cashflow_bp = make_grid_blueprint(
    name="cashflow",
    page_url="/cashflow",
    api_url="/api/cashflow",
    store=GridStore(table="income_record", label_col="description", month_col="date", month_is_text=False),
    page={
        "title": "CashFlow",
        "label": "Description",
        "add_placeholder": "New description",
        "add_button": "+ Add row",
        "chart_title": "Annual cash flow by description",
        "empty_noun": "cash flow records",
        "allow_negative": True,
    },
    before_read=sync_target_allocation,
    locked=locked_months,
)
