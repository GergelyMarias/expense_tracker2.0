######################
# routes/auth.py
######################
from flask import Blueprint, render_template, request, redirect, session

# App configuration (password, secret key, etc.)
from config import APP_PASSWORD


auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/", methods=["GET", "POST"])
def login():
    """Login page. 'Hide amounts' chosen here blurs money values on every page for this session."""
    if session.get("logged_in"):
        return redirect("/dashboard")

    error = None
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session["logged_in"] = True
            session["hide_amounts"] = request.form.get("hide_amounts") == "on"
            return redirect("/dashboard")
        error = "Wrong password."

    return render_template("login.html", error=error)


@auth_bp.route("/logout")
def logout():
    """
    Logout the user and redirect to login page.
    """
    session.clear()
    return redirect("/")
