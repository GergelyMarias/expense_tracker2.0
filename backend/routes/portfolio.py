from flask import Blueprint, render_template, request, jsonify, session

import portfolio_service as svc

portfolio_bp = Blueprint("portfolio", __name__)


def _guard():
    return None if session.get("logged_in") else (jsonify(error="unauthorized"), 401)


def _json_error(e, code=400):
    return jsonify(error=str(e)), code


@portfolio_bp.route("/portfolio")
def portfolio_page():
    # The page loads its data from the API, so slow Yahoo calls never block the first paint
    return render_template("portfolio.html")


# ------------------------------ [Overview data] ------------------------------
@portfolio_bp.route("/api/portfolio/summary")
def api_summary():
    if (g := _guard()): return g
    try:
        return jsonify(svc.summary())
    except Exception as e:
        return _json_error(f"Could not value the portfolio: {e}", 502)


@portfolio_bp.route("/api/portfolio/history")
def api_history():
    if (g := _guard()): return g
    try:
        return jsonify(svc.history())
    except Exception as e:
        return _json_error(f"Could not build history: {e}", 502)


# ------------------------------ [Transactions] ------------------------------
@portfolio_bp.route("/api/portfolio/tx", methods=["POST"])
def api_tx_create():
    if (g := _guard()): return g
    try:
        return jsonify(ok=True, tx=svc.save_tx(request.get_json(silent=True) or {}))
    except ValueError as e:
        return _json_error(e)


@portfolio_bp.route("/api/portfolio/tx/<int:tx_id>", methods=["PUT", "DELETE"])
def api_tx_change(tx_id):
    if (g := _guard()): return g
    if request.method == "DELETE":
        svc.delete_tx(tx_id)
        return jsonify(ok=True)
    try:
        return jsonify(ok=True, tx=svc.save_tx(request.get_json(silent=True) or {}, tx_id))
    except ValueError as e:
        return _json_error(e)
    except LookupError as e:
        return _json_error(e, 404)


# ------------------------------ [Manual prices] ------------------------------
@portfolio_bp.route("/api/portfolio/prices", methods=["POST"])
def api_price_create():
    if (g := _guard()): return g
    try:
        svc.save_price(request.get_json(silent=True) or {})
        return jsonify(ok=True)
    except ValueError as e:
        return _json_error(e)


@portfolio_bp.route("/api/portfolio/prices/<int:pid>", methods=["DELETE"])
def api_price_delete(pid):
    if (g := _guard()): return g
    svc.delete_price(pid)
    return jsonify(ok=True)


# ------------------------------ [Yahoo lookups] ------------------------------
@portfolio_bp.route("/api/portfolio/search")
def api_search():
    if (g := _guard()): return g
    try:
        return jsonify(results=svc.search(request.args.get("q", "")))
    except Exception as e:
        return _json_error(f"Yahoo search failed: {e}", 502)


@portfolio_bp.route("/api/portfolio/quote")
def api_quote():
    if (g := _guard()): return g
    sym = request.args.get("symbol", "")
    if not sym:
        return _json_error("symbol is required")
    try:
        return jsonify(svc.quote(sym))
    except Exception as e:
        return _json_error(f"No quote for {sym}: {e}", 502)
