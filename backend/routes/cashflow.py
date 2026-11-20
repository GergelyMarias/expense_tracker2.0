from routes.month_grid import GridStore, make_grid_blueprint

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
)
