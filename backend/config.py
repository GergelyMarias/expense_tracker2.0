import os

APP_PASSWORD = os.environ.get("APP_PASSWORD") or "pass"
SECRET_KEY = os.environ.get("SECRET_KEY") or "dev_secret_key"
DATABASE_URL = os.environ.get("DATABASE_URL")
