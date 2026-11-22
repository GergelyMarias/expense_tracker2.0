#######################
# routes/__init__.py
#######################

from flask import Flask
from routes.auth import auth_bp
from routes.expenses import expenses_bp
from routes.targets import targets_bp
from routes.cashflow import cashflow_bp
from routes.api import api_bp
from routes.money import money_bp
from routes.portfolio import portfolio_bp

def register_routes(app: Flask):
    app.register_blueprint(auth_bp)
    app.register_blueprint(expenses_bp)
    app.register_blueprint(targets_bp)
    app.register_blueprint(cashflow_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(money_bp)
    app.register_blueprint(portfolio_bp)