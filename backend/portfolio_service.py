"""Portfolio: transactions, valuation and history.

Transaction types (table portfolio_tx):
  fx     - currency conversion log: currency/amount -> to_currency/to_amount (rate is derived, never stored)
  yahoo  - security priced by Yahoo Finance: symbol, currency, quantity, amount (total paid / received)
  manual - security not on Yahoo: name, currency, quantity, price, amount; later prices from portfolio_price
  zcb    - zero-coupon bond: name, currency, amount (invested), face_value, maturity; accrues linearly

Valuation rules:
  * Securities use average cost; a SELL removes cost proportionally and books realized P/L.
  * 'manual' securities are valued at the latest manual price on/before the valuation date
    (falls back to the latest transaction price).
  * Zero-coupon bonds: value = invested + (face - invested) * elapsed / term; dropped after maturity.
  * Everything is reported in HUF. Cost uses the FX rate of the transaction date, value uses the
    rate of the valuation date, so P/L includes currency moves.
"""
import time
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

from db import get_cursor, SQL_PLACEHOLDER as P
from utils import get_huf_rate

TYPES = ("fx", "yahoo", "manual", "zcb")
COLUMNS = ("date", "type", "side", "symbol", "name", "currency", "quantity", "price",
           "amount", "to_currency", "to_amount", "face_value", "maturity", "note")

# ------------------------------ [small TTL cache for Yahoo calls] ------------------------------
_cache = {}

def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val

def invalidate():
    """Drop cached history after any data change (live prices stay cached)."""
    for k in [k for k in _cache if k[0] == "history"]:
        _cache.pop(k, None)


def _d(v):
    """date-ish -> date"""
    if v is None or v == "":
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()

def _f(v):
    return None if v is None or v == "" else float(v)


# ------------------------------ [transactions CRUD] ------------------------------
def list_tx():
    with get_cursor() as c:
        c.execute("SELECT * FROM portfolio_tx ORDER BY date DESC, id DESC")
        rows = c.fetchall()
    out = []
    for r in rows:
        r = dict(r)
        for k in ("quantity", "price", "amount", "to_amount", "face_value"):
            r[k] = _f(r.get(k))
        r["date"] = str(r["date"])[:10]
        r["maturity"] = str(r["maturity"])[:10] if r.get("maturity") else None
        out.append(r)
    return out

def _held_qty(kind, key, currency, as_of, exclude_id=None):
    """Quantity of a security held on as_of (for validating sells)."""
    qty = 0.0
    for t in list_tx():
        if t["type"] != kind or t["id"] == exclude_id or _d(t["date"]) > as_of:
            continue
        if kind == "yahoo" and t["symbol"] != key:
            continue
        if kind == "manual" and (t["name"] != key or t["currency"] != currency):
            continue
        qty += t["quantity"] if t["side"] == "BUY" else -t["quantity"]
    return qty

def validate_tx(data, tx_id=None):
    """Return a clean dict for the DB or raise ValueError with a readable message."""
    t = (data.get("type") or "").strip()
    if t not in TYPES:
        raise ValueError("Unknown transaction type.")
    try:
        d = _d(data.get("date"))
    except ValueError:
        d = None
    if not d:
        raise ValueError("Date is required.")

    def money(k, label, positive=True):
        try:
            v = float(str(data.get(k, "")).replace(" ", "").replace(",", "."))
        except ValueError:
            raise ValueError(f"{label} must be a number.")
        if positive and v <= 0:
            raise ValueError(f"{label} must be greater than 0.")
        return v

    def ccy(k, label):
        v = (data.get(k) or "").strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError(f"{label} must be a 3-letter currency code.")
        return v

    row = {c: None for c in COLUMNS}
    row.update(date=d.isoformat(), type=t, note=(data.get("note") or "").strip()[:300] or None)

    if t == "fx":
        row.update(currency=ccy("currency", "From currency"), amount=money("amount", "From amount"),
                   to_currency=ccy("to_currency", "To currency"), to_amount=money("to_amount", "To amount"), side=None)
        if row["currency"] == row["to_currency"]:
            raise ValueError("From and to currency must differ.")

    elif t in ("yahoo", "manual"):
        side = (data.get("side") or "BUY").upper()
        if side not in ("BUY", "SELL"):
            raise ValueError("Side must be BUY or SELL.")
        row.update(side=side, currency=ccy("currency", "Currency"),
                   quantity=money("quantity", "Quantity"), amount=money("amount", "Total amount"))
        if t == "yahoo":
            sym = (data.get("symbol") or "").strip().upper()
            if not sym:
                raise ValueError("Pick a security from the Yahoo search.")
            row.update(symbol=sym, name=(data.get("name") or sym).strip()[:200])
            key = sym
        else:
            name = (data.get("name") or "").strip()[:200]
            if not name:
                raise ValueError("Security name is required.")
            row.update(name=name, price=money("price", "Price"))
            key = name
        if side == "SELL":
            held = _held_qty(t, key, row["currency"], d, exclude_id=tx_id)
            if row["quantity"] > held + 1e-9:
                raise ValueError(f"Cannot sell {row['quantity']:g}: only {held:g} held on {d}.")

    elif t == "zcb":
        name = (data.get("name") or "").strip()[:200]
        if not name:
            raise ValueError("Bond name is required.")
        row.update(side="BUY", name=name, currency=ccy("currency", "Currency"),
                   amount=money("amount", "Invested amount"), face_value=money("face_value", "Face value"))
        try:
            m = _d(data.get("maturity"))
        except ValueError:
            m = None
        if not m or m <= d:
            raise ValueError("Maturity must be after the purchase date.")
        if row["face_value"] < row["amount"]:
            raise ValueError("Face value is lower than the invested amount.")
        row["maturity"] = m.isoformat()
    return row

def save_tx(data, tx_id=None):
    row = validate_tx(data, tx_id)
    cols = list(COLUMNS)
    with get_cursor() as c:
        if tx_id:
            c.execute(f"UPDATE portfolio_tx SET {', '.join(f'{k}={P}' for k in cols)} WHERE id={P}",
                      tuple(row[k] for k in cols) + (tx_id,))
            if c.rowcount == 0:
                raise LookupError("Transaction not found.")
        else:
            c.execute(f"INSERT INTO portfolio_tx ({', '.join(cols)}) VALUES ({', '.join([P] * len(cols))})",
                      tuple(row[k] for k in cols))
    invalidate()
    return row

def delete_tx(tx_id):
    with get_cursor() as c:
        c.execute(f"DELETE FROM portfolio_tx WHERE id={P}", (tx_id,))
    invalidate()


# ------------------------------ [manual prices] ------------------------------
def list_prices():
    with get_cursor() as c:
        c.execute("SELECT * FROM portfolio_price ORDER BY date DESC, id DESC")
        return [{**dict(r), "date": str(r["date"])[:10], "price": float(r["price"])} for r in c.fetchall()]

def save_price(data):
    name = (data.get("name") or "").strip()
    cur = (data.get("currency") or "").strip().upper()
    try:
        d = _d(data.get("date"))
        price = float(str(data.get("price")).replace(",", "."))
    except (ValueError, TypeError):
        raise ValueError("Date and price are required.")
    if not name or len(cur) != 3 or price <= 0:
        raise ValueError("Pick a security and enter a positive price.")
    with get_cursor() as c:
        c.execute(f"INSERT INTO portfolio_price (name, currency, date, price) VALUES ({P},{P},{P},{P})",
                  (name, cur, d.isoformat(), price))
    invalidate()

def delete_price(pid):
    with get_cursor() as c:
        c.execute(f"DELETE FROM portfolio_price WHERE id={P}", (pid,))
    invalidate()


# ------------------------------ [Yahoo lookups] ------------------------------
def search(q):
    q = (q or "").strip()
    if len(q) < 2:
        return []
    def run():
        res = yf.Search(q, max_results=10, news_count=0).quotes
        return [{"symbol": r["symbol"], "name": r.get("longname") or r.get("shortname") or r["symbol"],
                 "exchange": r.get("exchDisp") or r.get("exchange") or "", "type": r.get("typeDisp") or r.get("quoteType") or ""}
                for r in res if r.get("symbol")]
    return _cached(("search", q.lower()), 3600, run)

def quote(symbol):
    symbol = symbol.strip().upper()
    def run():
        fi = yf.Ticker(symbol).fast_info
        return {"symbol": symbol, "currency": (fi["currency"] or "").upper(),
                "price": float(fi["lastPrice"]), "previous_close": float(fi["previousClose"] or 0)}
    return _cached(("quote", symbol), 600, run)

def _live_prices(symbols):
    """{symbol: (last, previous_close)} for all held Yahoo symbols, one download call, cached 10 min."""
    symbols = sorted(set(symbols))
    if not symbols:
        return {}
    def run():
        df = yf.download(symbols, period="7d", progress=False, auto_adjust=False)
        close = df["Close"]
        if isinstance(close, pd.Series):
            close = close.to_frame(symbols[0])
        out = {}
        for s in symbols:
            col = close[s].dropna() if s in close else pd.Series(dtype=float)
            if len(col):
                out[s] = (float(col.iloc[-1]), float(col.iloc[-2]) if len(col) > 1 else float(col.iloc[-1]))
        return out
    return _cached(("live", tuple(symbols)), 600, run)

def _fx_now(ccy):
    if ccy == "HUF":
        return 1.0
    return _cached(("fxnow", ccy), 600, lambda: get_huf_rate(ccy, date.today().isoformat()))


# ------------------------------ [valuation core] ------------------------------
def compute(txs, prices, as_of, price_at, fx_at):
    """Positions as of `as_of`.
    price_at(symbol) -> (price, previous) or None; fx_at(ccy, date) -> HUF per unit."""
    secs, bonds, matured, fx_log = {}, [], [], []
    for t in sorted(txs, key=lambda x: (x["date"], x["id"])):
        td = _d(t["date"])
        if td > as_of:
            continue
        if t["type"] == "fx":
            fx_log.append(t)
            continue
        if t["type"] == "zcb":
            (matured if _d(t["maturity"]) <= as_of else bonds).append(t)
            continue
        key = ("yahoo", t["symbol"]) if t["type"] == "yahoo" else ("manual", t["name"], t["currency"])
        p = secs.setdefault(key, {"type": t["type"], "symbol": t.get("symbol"), "name": t["name"], "currency": t["currency"],
                                  "qty": 0.0, "cost_ccy": 0.0, "cost_huf": 0.0, "realized_huf": 0.0,
                                  "last_tx_price": None, "last_tx_date": None, "first_date": t["date"], "tx_count": 0})
        p["tx_count"] += 1
        rate = fx_at(t["currency"], td)
        unit = t["amount"] / t["quantity"] if t["quantity"] else 0
        if t["side"] == "BUY":
            p["qty"] += t["quantity"]; p["cost_ccy"] += t["amount"]; p["cost_huf"] += t["amount"] * rate
        else:
            frac = min(t["quantity"] / p["qty"], 1) if p["qty"] else 0
            removed_huf = p["cost_huf"] * frac
            p["cost_ccy"] -= p["cost_ccy"] * frac; p["cost_huf"] -= removed_huf; p["qty"] -= t["quantity"]
            p["realized_huf"] += t["amount"] * rate - removed_huf
        p["last_tx_price"], p["last_tx_date"] = (t["price"] or unit), t["date"]

    positions, realized = [], 0.0
    for key, p in secs.items():
        realized += p["realized_huf"]
        if p["qty"] <= 1e-9:
            continue
        prev = None
        price_date = None
        if p["type"] == "yahoo":
            q = price_at(p["symbol"])
            price, prev = q if q else (p["cost_ccy"] / p["qty"], None)
            price_date = as_of.isoformat() if q else None
        else:
            upd = [x for x in prices if x["name"] == p["name"] and x["currency"] == p["currency"] and _d(x["date"]) <= as_of]
            latest = max(upd, key=lambda x: (x["date"], x["id"])) if upd else None
            if latest and latest["date"] >= (p["last_tx_date"] or ""):
                price, price_date = latest["price"], latest["date"]
            else:
                price, price_date = p["last_tx_price"], p["last_tx_date"]
        rate = fx_at(p["currency"], as_of)
        value_ccy = p["qty"] * price
        positions.append({
            "key": "|".join(str(k) for k in key), "type": p["type"], "symbol": p["symbol"], "name": p["name"],
            "currency": p["currency"], "quantity": p["qty"], "price": price, "price_date": price_date,
            "avg_cost": p["cost_ccy"] / p["qty"], "cost_ccy": p["cost_ccy"], "cost_huf": p["cost_huf"],
            "value_ccy": value_ccy, "value_huf": value_ccy * rate,
            "day_change_huf": (price - prev) * p["qty"] * rate if prev else 0.0,
            "realized_huf": p["realized_huf"], "since": p["first_date"],
            "stale_days": (as_of - _d(price_date)).days if (p["type"] == "manual" and price_date) else None,
        })

    for b in bonds:
        start, end = _d(b["date"]), _d(b["maturity"])
        progress = max(0.0, min(1.0, (as_of - start).days / max((end - start).days, 1)))
        value_ccy = b["amount"] + (b["face_value"] - b["amount"]) * progress
        rate = fx_at(b["currency"], as_of)
        cost_huf = b["amount"] * fx_at(b["currency"], start)
        positions.append({
            "key": f"zcb|{b['id']}", "type": "zcb", "symbol": None, "name": b["name"], "currency": b["currency"],
            "quantity": None, "price": None, "price_date": None, "avg_cost": None,
            "cost_ccy": b["amount"], "cost_huf": cost_huf, "value_ccy": value_ccy, "value_huf": value_ccy * rate,
            "day_change_huf": 0.0, "realized_huf": 0.0, "since": b["date"], "stale_days": None,
            "face_value": b["face_value"], "maturity": b["maturity"], "progress": progress,
            "yield_pa": ((b["face_value"] / b["amount"]) ** (365 / max((end - start).days, 1)) - 1) * 100,
            "days_left": (end - as_of).days, "tx_id": b["id"],
        })
    for p in positions:
        p["pl_huf"] = p["value_huf"] - p["cost_huf"]
        p["pl_pct"] = (p["pl_huf"] / p["cost_huf"] * 100) if p["cost_huf"] else 0.0
    return positions, matured, fx_log, realized


def summary():
    txs, prices = list_tx(), list_prices()
    today = date.today()
    live = _live_prices([t["symbol"] for t in txs if t["type"] == "yahoo"])
    hist_rate = lambda c, d: 1.0 if c == "HUF" else get_huf_rate(c, d.isoformat())
    fx_at = lambda c, d: _fx_now(c) if d >= today else hist_rate(c, d)
    positions, matured, fx_log, realized = compute(txs, prices, today, lambda s: live.get(s), fx_at)

    total_value = sum(p["value_huf"] for p in positions)
    total_cost = sum(p["cost_huf"] for p in positions)
    for p in positions:
        p["weight"] = p["value_huf"] / total_value * 100 if total_value else 0

    fx_rows = []
    for t in fx_log:
        implied = t["to_amount"] / t["amount"]             # to-units per 1 from-unit, as traded
        now = _fx_now(t["currency"]) / _fx_now(t["to_currency"])  # same, today
        to_now_in_from = t["to_amount"] / now               # what the received amount is worth back in "from" today
        gain_huf = (to_now_in_from - t["amount"]) * _fx_now(t["currency"])
        fx_rows.append({**t, "implied_rate": implied, "rate_now": now, "gain_huf": gain_huf,
                        "gain_pct": (to_now_in_from / t["amount"] - 1) * 100})

    matured_rows = [{"id": b["id"], "name": b["name"], "currency": b["currency"], "invested": b["amount"],
                     "face_value": b["face_value"], "maturity": b["maturity"],
                     "gain_huf": (b["face_value"] - b["amount"]) * hist_rate(b["currency"], _d(b["maturity"]))} for b in matured]

    manual_assets = sorted({(t["name"], t["currency"]) for t in txs if t["type"] == "manual"})
    return {
        "as_of": today.isoformat(),
        "totals": {"value_huf": total_value, "cost_huf": total_cost, "pl_huf": total_value - total_cost,
                   "pl_pct": (total_value / total_cost - 1) * 100 if total_cost else 0,
                   "day_change_huf": sum(p["day_change_huf"] for p in positions), "realized_huf": realized,
                   "positions": len(positions)},
        "positions": sorted(positions, key=lambda p: -p["value_huf"]),
        "matured": matured_rows,
        "fx": fx_rows,
        "manual_assets": [{"name": n, "currency": c} for n, c in manual_assets],
        "prices": prices,
        "transactions": txs,
    }


# ------------------------------ [history: value vs invested over time] ------------------------------
def history():
    def run():
        txs, prices = list_tx(), list_prices()
        holdings = [t for t in txs if t["type"] != "fx"]
        if not holdings:
            return {"dates": [], "value": [], "invested": []}
        start = min(_d(t["date"]) for t in holdings)
        today = date.today()
        syms = sorted({t["symbol"] for t in holdings if t["type"] == "yahoo"})
        ccys = sorted({t["currency"] for t in holdings if t["currency"] != "HUF"})
        tickers = syms + [f"{c}HUF=X" for c in ccys]
        close = pd.DataFrame()
        if tickers:
            df = yf.download(tickers, start=(start - timedelta(days=10)).isoformat(), progress=False, auto_adjust=False)
            close = df["Close"]
            if isinstance(close, pd.Series):
                close = close.to_frame(tickers[0])
            close = close.ffill()
            close.index = close.index.tz_localize(None) if close.index.tz is not None else close.index

        def at(col, d):
            if col not in close:
                return None
            s = close[col].loc[:pd.Timestamp(d)].dropna()
            return float(s.iloc[-1]) if len(s) else None

        fx_at = lambda c, d: 1.0 if c == "HUF" else (at(f"{c}HUF=X", d) or get_huf_rate(c, d.isoformat()))
        # Month ends from the first transaction to today, plus today
        points, d = [], start
        while d < today:
            nxt = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
            points.append(min(nxt - timedelta(days=1), today))
            d = nxt
        if not points or points[-1] != today:
            points.append(today)
        points = [start] + [p for p in points if p > start]

        dates, value, invested = [], [], []
        for p in points:
            pos, _, _, _ = compute(txs, prices, p, lambda s: ((at(s, p), None) if at(s, p) else None), fx_at)
            dates.append(p.isoformat())
            value.append(round(sum(x["value_huf"] for x in pos)))
            invested.append(round(sum(x["cost_huf"] for x in pos)))
        return {"dates": dates, "value": value, "invested": invested}
    return _cached(("history",), 3600, run)
