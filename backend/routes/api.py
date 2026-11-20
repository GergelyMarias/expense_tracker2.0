from flask import Blueprint, jsonify
from db import get_categories_for_month

api_bp = Blueprint("api", __name__, url_prefix="/api")

@api_bp.route("/categories/<month>")
def categories(month):
    """
    Returns a JSON-safe list of category names.
    Extracts strings from DB Row objects to avoid Serialization errors.
    """
    try:
        # get_categories_for_month returns a list of Row/Dict objects
        raw_rows = get_categories_for_month(month)
        
        # Convert to a flat list of strings for the frontend
        category_names = [row['category'] for row in raw_rows]
        
        return jsonify({
            "categories": category_names,
            "has_targets": len(category_names) > 0
        })
    except Exception as e:
        return jsonify({"error": str(e), "categories": []}), 500