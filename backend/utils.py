######################
# utils.py
######################

from datetime import date, datetime, timedelta
import yfinance as yf

# Formatting Helpers
def huf(value):
    try:
        return format_amount(float(value)) + " Ft"
    except (ValueError, TypeError):
        return "0 Ft"


# Date Helpers
def today_iso():
    return date.today().isoformat()

def month_start(date_obj=None):
    if date_obj is None:
        date_obj = date.today()
    return date_obj.replace(day=1).isoformat()

def month_str(date_obj=None):
    if date_obj is None:
        date_obj = date.today()
    return date_obj.strftime("%Y-%m")

def parse_date(d):
    return datetime.strptime(d, "%Y-%m-%d").date()


# FX Conversion
def convert_to_huf(amount, currency, d):
    if currency == "HUF":
        return float(amount)
    date_obj = datetime.strptime(d, "%Y-%m-%d")
    data = yf.download(f"{currency}HUF=X",
                       start=date_obj - timedelta(days=7),
                       end=date_obj + timedelta(days=1),
                       progress=False)
    if data.empty:
        raise ValueError("FX data unavailable")
    series = data["Close"].ffill()
    rate = series[series.index <= date_obj].iloc[-1]
    return round(float(amount) * float(rate), 2)

def format_amount(value):
    """
    Format number as: 000.000.000,00
    - thousands: dot
    - decimals: comma
    - max 2 decimals
    - show decimals only if they exist
    Works even if input uses ',' as decimal separator.
    """
    if value is None or value == "":
        return ""

    # Convert string to float, handling comma decimal
    if isinstance(value, str):
        value = value.replace(".", "")   # remove any thousand separators
        value = value.replace(",", ".")  # convert decimal to dot

    try:
        value = float(value)
    except (TypeError, ValueError):
        return ""

    # Round to 2 decimals
    value = round(value, 2)

    # Separate integer and decimal
    integer_part = int(value)
    decimal_part = value - integer_part

    # Format integer with thousands separator
    int_str = f"{integer_part:,}".replace(",", ".")

    if decimal_part == 0:
        return int_str
    else:
        # Keep up to 2 decimals, remove trailing zero if needed
        dec_str = f"{decimal_part:.2f}"[1:]  # ".05"
        dec_str = dec_str.replace(".", ",")
        return int_str + dec_str



