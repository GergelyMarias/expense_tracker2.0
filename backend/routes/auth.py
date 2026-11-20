######################
# routes/auth.py
######################
from flask import Blueprint, render_template, request, redirect, session

# App configuration (password, secret key, etc.)
from config import APP_PASSWORD


auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/", methods=["GET", "POST"])
def login():
    """
    Login page. Redirects to /expenses if already logged in.
    """
    if session.get("logged_in"):
        return redirect("/expenses")

    if request.method == "POST" and request.form.get("password") == APP_PASSWORD:
        session["logged_in"] = True
        return redirect("/expenses")

    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    """
    Logout the user and redirect to login page.
    """
    session.clear()
    return redirect("/")
