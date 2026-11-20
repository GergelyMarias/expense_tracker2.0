from routes.month_grid import GridStore, make_grid_blueprint

# Expense Targets: rows = category, one 'YYYY-MM' target per category per month
targets_bp = make_grid_blueprint(
    name="targets",
    page_url="/targets",
    api_url="/api/targets",
    store=GridStore(table="expense_target", label_col="category", month_col="month", month_is_text=True),
    page={
        "title": "Expense Targets",
        "label": "Category",
        "add_placeholder": "New category name",
        "add_button": "+ Add category",
        "chart_title": "Annual targets by category",
        "empty_noun": "targets",
        "allow_negative": False,
    },
)
