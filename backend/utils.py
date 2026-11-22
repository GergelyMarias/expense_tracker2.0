######################
# utils.py
######################

from datetime import datetime, timedelta
import yfinance as yf

# Formatting Helpers
def huf(value):
    try:
        return format_amount(float(value)) + " Ft"
    except (ValueError, TypeError):
        return "0 Ft"


# FX Conversion
_rate_cache = {}  # (currency, 'YYYY-MM-DD') -> rate; past closes never change

def get_huf_rate(currency, d):
    """HUF per 1 unit of currency on date d (last Yahoo Finance close on or before d).
    Future dates use the latest available close."""
    if currency == "HUF":
        return 1.0
    date_obj = datetime.strptime(str(d)[:10], "%Y-%m-%d")
    today = datetime.now()
    lookup = min(date_obj, today)
    key = (currency, lookup.strftime("%Y-%m-%d"))
    if key in _rate_cache:
        return _rate_cache[key]

    data = yf.download(f"{currency}HUF=X",
                       start=lookup - timedelta(days=10),
                       end=lookup + timedelta(days=1),
                       progress=False)
    if data is None or data.empty:
        raise ValueError(f"{currency}/HUF exchange rate unavailable for {d}")
    close = data["Close"]
    # Newer yfinance returns Close as a one-column DataFrame (ticker columns): take that column
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    close = close.ffill().dropna()
    close = close[close.index <= lookup]
    if close.empty:
        raise ValueError(f"{currency}/HUF exchange rate unavailable for {d}")
    # .iloc[-1] on a Series is a scalar; float(Series) raises TypeError on pandas 3
    rate = round(float(close.iloc[-1]), 4)
    if lookup.date() < today.date():  # today's rate can still move: don't cache it
        _rate_cache[key] = rate
    return rate

def convert_to_huf(amount, currency, d):
    return round(float(amount) * get_huf_rate(currency, d), 2)

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



