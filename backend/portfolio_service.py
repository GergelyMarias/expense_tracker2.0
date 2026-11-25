"""Portfolio: transactions, valuation and history.

Transaction types (table portfolio_tx):
  fx     - currency conversion: currency/amount -> to_currency/to_amount (rate is derived, never stored).
           Foreign currency received is held as a CASH position (the portfolio is HUF-denominated):
             HUF -> X : X cash grows, its invested cost = the HUF paid
             X -> Y   : X cash shrinks (average cost moves along to Y); if more X is converted than held,
                        the shortfall counts as new money in at that day's rate
             X -> HUF : X cash leaves the portfolio; HUF received minus its cost = realized P/L
  yahoo  - security priced by Yahoo Finance: symbol, currency, quantity, amount (total paid / received)
  manual - security not on Yahoo: name, currency, quantity, amount (price = amount / quantity);
           later updates in portfolio_price give quantity + current value (price = value / quantity).
           An update's quantity replaces the held quantity from that date (e.g. reinvested units, no cost).
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
           "amount", "to_currency", "to_amount", "face_value", "maturity", "note", "external", "platform")

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
        r["external"] = 1 if r.get("external") else 0
        out.append(r)
    return out

def _held_qty(kind, key, currency, as_of, exclude_id=None):
    """Quantity of a security held on as_of (for validating sells)."""
    ev = []
    for t in list_tx():
        if t["type"] != kind or t["id"] == exclude_id or _d(t["date"]) > as_of:
            continue
        if kind == "yahoo" and t["symbol"] != key:
            continue
        if kind == "manual" and (t["name"] != key or t["currency"] != currency):
            continue
        ev.append((t["date"], 0, t["id"], t["quantity"] if t["side"] == "BUY" else -t["quantity"], False))
    if kind == "manual":
        ev += [(u["date"], 1, u["id"], u["quantity"], True) for u in list_prices()
               if u["name"] == key and u["currency"] == currency and u.get("quantity") and _d(u["date"]) <= as_of]
    qty = 0.0
    for _, _, _, q, reset in sorted(ev):
        qty = q if reset else qty + q
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
    row.update(date=d.isoformat(), type=t, note=(data.get("note") or "").strip()[:300] or None, external=0)
    row["platform"] = (data.get("platform") or "").strip()[:100] or None
    wants_external = str(data.get("external", "")).lower() in ("1", "true", "on", "yes")

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
            row.update(name=name, price=row["amount"] / row["quantity"])
            key = name
        row["external"] = 1 if (wants_external and side == "BUY" and row["currency"] != "HUF") else 0
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
        row["external"] = 1 if (wants_external and row["currency"] != "HUF") else 0
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
        rows = [dict(r) for r in c.fetchall()]
    return [{**r, "date": str(r["date"])[:10], "price": float(r["price"]),
             "quantity": _f(r.get("quantity")), "value": _f(r.get("value"))} for r in rows]

def save_price(data):
    name = (data.get("name") or "").strip()
    cur = (data.get("currency") or "").strip().upper()
    try:
        d = _d(data.get("date"))
        qty = float(str(data.get("quantity")).replace(",", "."))
        value = float(str(data.get("value")).replace(",", "."))
    except (ValueError, TypeError):
        raise ValueError("Date, quantity and current value are required.")
    if not d or not name or len(cur) != 3 or qty <= 0 or value <= 0:
        raise ValueError("Pick a security and enter a positive quantity and value.")
    with get_cursor() as c:
        c.execute(f"INSERT INTO portfolio_price (name, currency, date, price, quantity, value) VALUES ({P},{P},{P},{P},{P},{P})",
                  (name, cur, d.isoformat(), value / qty, qty, value))
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
    price_at(symbol) -> (price, previous) or None; fx_at(ccy, date) -> HUF per unit.

    The portfolio is HUF-denominated, so every foreign-currency movement goes through a cash
    ledger per currency (balance + HUF cost, average-cost method):
      * FX change HUF -> X adds X cash; X -> HUF removes it (realized P/L); X -> Y moves the cost.
      * Buying a non-HUF security or bond spends that currency's cash; the security inherits the
        HUF cost of the cash used. Selling (or a bond maturing) puts the proceeds back into cash.
      * Spending more cash than held: the shortfall counts as new money in at that day's rate.
      * external=1 buys ("paid with money from outside") do not touch cash: as if the currency arrived
        and was spent the same day. Cost = amount x HUF rate of the purchase date.
      * HUF trades and HUF bond payouts go straight in / out of the portfolio (no HUF cash)."""
    secs, bonds, matured, fx_log = {}, [], [], []
    cash = {}                 # currency -> {"bal": units, "cost_huf": HUF invested, "first": date}
    realized_other = 0.0      # FX back to HUF + matured bonds

    def take_cash(ccy, amount, d):
        """Spend `amount` of foreign cash; returns the HUF cost that leaves the cash position."""
        c = cash.setdefault(ccy, {"bal": 0.0, "cost_huf": 0.0, "first": d.isoformat()})
        used = min(amount, max(c["bal"], 0.0))
        removed = c["cost_huf"] * (used / c["bal"]) if c["bal"] > 1e-9 else 0.0
        c["bal"] -= used
        c["cost_huf"] -= removed
        return removed + (amount - used) * fx_at(ccy, d)

    def add_cash(ccy, amount, cost_huf, d):
        c = cash.setdefault(ccy, {"bal": 0.0, "cost_huf": 0.0, "first": d.isoformat()})
        c["bal"] += amount
        c["cost_huf"] += cost_huf

    def buy_cost(t, d):
        """HUF cost of a purchase: HUF as paid; outside money at the day's rate; otherwise from cash."""
        if t["currency"] == "HUF":
            return t["amount"]
        if t.get("external"):
            return t["amount"] * fx_at(t["currency"], d)
        return take_cash(t["currency"], t["amount"], d)

    # Transactions plus bond maturities, in date order (same day: trades first, then payouts)
    events = [(t["date"], 0, t["id"], "tx", t) for t in txs if _d(t["date"]) <= as_of]
    events += [(t["maturity"], 1, t["id"], "mature", t) for t in txs
               if t["type"] == "zcb" and _d(t["maturity"]) <= as_of and _d(t["date"]) <= as_of]
    events += [(u["date"], 2, u["id"], "update", u) for u in prices if u.get("quantity") and _d(u["date"]) <= as_of]
    bond_cost = {}

    for _, _, _, kind, t in sorted(events, key=lambda e: e[:3]):
        if kind == "update":                       # manual update: quantity now held (extra units carry no cost)
            p = secs.get(("manual", t["name"], t["currency"]))
            if p:
                p["qty"] = t["quantity"]
            continue
        td = _d(t["date"])
        ccy = t["currency"]

        if kind == "mature":                       # face value is paid out
            md = _d(t["maturity"])
            cost = bond_cost.pop(t["id"], 0.0)
            proceeds_huf = t["face_value"] if ccy == "HUF" else t["face_value"] * fx_at(ccy, md)
            realized_other += proceeds_huf - cost
            if ccy != "HUF":
                add_cash(ccy, t["face_value"], proceeds_huf, md)
            matured.append({**t, "gain_huf": proceeds_huf - cost})
            continue

        if t["type"] == "fx":
            fx_log.append(t)
            src, dst = ccy, t["to_currency"]
            cost_out = t["amount"] if src == "HUF" else take_cash(src, t["amount"], td)
            if dst == "HUF":
                realized_other += t["to_amount"] - cost_out
            else:
                add_cash(dst, t["to_amount"], cost_out, td)
            continue

        if t["type"] == "zcb":
            bond_cost[t["id"]] = buy_cost(t, td)
            if _d(t["maturity"]) > as_of:
                bonds.append(t)
            continue

        key = ("yahoo", t["symbol"]) if t["type"] == "yahoo" else ("manual", t["name"], ccy)
        p = secs.setdefault(key, {"type": t["type"], "symbol": t.get("symbol"), "name": t["name"], "currency": ccy,
                                  "qty": 0.0, "cost_ccy": 0.0, "cost_huf": 0.0, "realized_huf": 0.0,
                                  "last_tx_price": None, "last_tx_date": None, "first_date": t["date"], "tx_count": 0})
        p["tx_count"] += 1
        unit = t["amount"] / t["quantity"] if t["quantity"] else 0
        if t["side"] == "BUY":
            cost_huf = buy_cost(t, td)
            p["qty"] += t["quantity"]
            p["cost_ccy"] += t["amount"]
            p["cost_huf"] += cost_huf
        else:
            frac = min(t["quantity"] / p["qty"], 1) if p["qty"] else 0
            removed_huf = p["cost_huf"] * frac
            p["cost_ccy"] -= p["cost_ccy"] * frac
            p["cost_huf"] -= removed_huf
            p["qty"] -= t["quantity"]
            proceeds_huf = t["amount"] if ccy == "HUF" else t["amount"] * fx_at(ccy, td)
            p["realized_huf"] += proceeds_huf - removed_huf
            if ccy != "HUF":
                add_cash(ccy, t["amount"], proceeds_huf, td)   # sale proceeds stay in the portfolio as cash
        p["last_tx_price"], p["last_tx_date"] = (t["price"] or unit), t["date"]

    positions, realized = [], realized_other
    for ccy, c in cash.items():
        if c["bal"] <= 1e-6:
            continue
        rate = fx_at(ccy, as_of)
        positions.append({
            "key": f"cash|{ccy}", "type": "cash", "symbol": None, "name": f"{ccy} cash", "currency": ccy,
            "quantity": c["bal"], "price": 1.0, "price_date": None,
            "avg_cost": c["cost_huf"] / c["bal"],          # average HUF paid per unit
            "rate_now": rate,
            "cost_ccy": c["bal"], "cost_huf": c["cost_huf"], "value_ccy": c["bal"], "value_huf": c["bal"] * rate,
            "day_change_huf": 0.0, "realized_huf": 0.0, "since": c["first"], "stale_days": None,
        })
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
        cost_huf = bond_cost.get(b["id"], b["amount"] * fx_at(b["currency"], start))
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
        rate = fx_at(p["currency"], as_of)
        p["rate_now"] = rate
        p["pl_huf"] = p["value_huf"] - p["cost_huf"]
        p["pl_pct"] = (p["pl_huf"] / p["cost_huf"] * 100) if p["cost_huf"] else 0.0
        # Split of the HUF P/L: investment return (in its own currency, at today's rate) + currency move
        p["pl_ccy"] = 0.0 if p["type"] == "cash" else p["value_ccy"] - p["cost_ccy"]
        p["pl_ccy_pct"] = (p["pl_ccy"] / p["cost_ccy"] * 100) if p["cost_ccy"] else 0.0
        p["inv_pl_huf"] = p["pl_ccy"] * rate
        p["fx_pl_huf"] = p["pl_huf"] - p["inv_pl_huf"]          # = cost_ccy x rate_now - cost_huf
        p["buy_rate"] = p["cost_huf"] / p["cost_ccy"] if p["cost_ccy"] else None
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
                     "face_value": b["face_value"], "maturity": b["maturity"], "gain_huf": b["gain_huf"]} for b in matured]

    # Currency exposure: every unit acquired (cash + what it was spent on), its average HUF rate
    # and the currency gain on it - independent of how the investments themselves performed.
    currencies = {}
    for p in positions:
        if p["currency"] == "HUF":
            continue
        c = currencies.setdefault(p["currency"], {"currency": p["currency"], "cash": 0.0, "invested_ccy": 0.0,
                                                  "holdings_value_ccy": 0.0, "units": 0.0, "cost_huf": 0.0,
                                                  "rate_now": p["rate_now"], "inv_pl_huf": 0.0, "positions": 0})
        if p["type"] == "cash":
            c["cash"] += p["value_ccy"]
        else:
            c["invested_ccy"] += p["cost_ccy"]
            c["holdings_value_ccy"] += p["value_ccy"]
            c["inv_pl_huf"] += p["inv_pl_huf"]
            c["positions"] += 1
        c["units"] += p["cost_ccy"]
        c["cost_huf"] += p["cost_huf"]
    for c in currencies.values():
        c["avg_rate"] = c["cost_huf"] / c["units"] if c["units"] else None
        c["fx_pl_huf"] = c["units"] * c["rate_now"] - c["cost_huf"]
        c["fx_pl_pct"] = (c["rate_now"] / c["avg_rate"] - 1) * 100 if c["avg_rate"] else 0.0
        c["exposure_ccy"] = c["cash"] + c["holdings_value_ccy"]
        c["exposure_huf"] = c["exposure_ccy"] * c["rate_now"]
        c["weight"] = c["exposure_huf"] / total_value * 100 if total_value else 0

    # Per platform: each platform is valued as its own sub-portfolio (its own cash ledger)
    by_platform = []
    # A fund held on several platforms is priced the same everywhere: latest price from any platform
    shared_prices = prices + [{"id": 0, "name": t["name"], "currency": t["currency"], "date": t["date"], "price": t["price"],
                               "quantity": None, "value": None} for t in txs if t["type"] == "manual" and t.get("price")]
    for plat in sorted({t.get("platform") or "" for t in txs}, key=lambda x: (x == "", x.lower())):
        sub = [t for t in txs if (t.get("platform") or "") == plat]
        pp, _, _, preal = compute(sub, shared_prices, today, lambda s: live.get(s), fx_at)
        v, c = sum(p["value_huf"] for p in pp), sum(p["cost_huf"] for p in pp)
        by_platform.append({
            "platform": plat or "No platform", "value_huf": v, "cost_huf": c, "pl_huf": v - c,
            "pl_pct": (v / c - 1) * 100 if c else 0.0, "realized_huf": preal,
            "inv_pl_huf": sum(p["inv_pl_huf"] for p in pp), "fx_pl_huf": sum(p["fx_pl_huf"] for p in pp),
            "day_change_huf": sum(p["day_change_huf"] for p in pp), "tx_count": len(sub),
            "weight": v / total_value * 100 if total_value else 0,
            "currencies": sorted({p["currency"] for p in pp}),
            "positions": [{k: p[k] for k in ("name", "symbol", "type", "currency", "value_huf", "cost_huf", "pl_huf", "pl_pct",
                                              "pl_ccy", "pl_ccy_pct", "fx_pl_huf", "value_ccy", "quantity")}
                          for p in sorted(pp, key=lambda p: -p["value_huf"])],
        })
    by_platform.sort(key=lambda b: -b["value_huf"])

    manual_assets = sorted({(t["name"], t["currency"]) for t in txs if t["type"] == "manual"})
    return {
        "as_of": today.isoformat(),
        "totals": {"value_huf": total_value, "cost_huf": total_cost, "pl_huf": total_value - total_cost,
                   "pl_pct": (total_value / total_cost - 1) * 100 if total_cost else 0,
                   "day_change_huf": sum(p["day_change_huf"] for p in positions), "realized_huf": realized,
                   "inv_pl_huf": sum(p["inv_pl_huf"] for p in positions), "fx_pl_huf": sum(p["fx_pl_huf"] for p in positions),
                   "positions": len(positions)},
        "positions": sorted(positions, key=lambda p: -p["value_huf"]),
        "matured": matured_rows,
        "fx": fx_rows,
        "currencies": sorted(currencies.values(), key=lambda c: -c["exposure_huf"]),
        "by_platform": by_platform,
        "platforms": sorted({t["platform"] for t in txs if t.get("platform")}, key=str.lower),
        "manual_assets": [{"name": n, "currency": c} for n, c in manual_assets],
        "prices": prices,
        "transactions": txs,
    }


# ------------------------------ [history: value vs invested over time] ------------------------------
def history():
    def run():
        txs, prices = list_tx(), list_prices()
        holdings = txs  # FX changes create cash positions, so they count too
        if not holdings:
            return {"dates": [], "value": [], "invested": []}
        start = min(_d(t["date"]) for t in holdings)
        today = date.today()
        syms = sorted({t["symbol"] for t in holdings if t["type"] == "yahoo"})
        ccys = sorted({c for t in holdings for c in (t["currency"], t.get("to_currency")) if c and c != "HUF"})
        tickers = syms + [f"{c}HUF=X" for c in ccys]
        close = pd.DataFrame()
        if tickers:
            df = yf.download(tickers, start=(start - timedelta(days=10)).isoformat(), progress=False, auto_adjust=False)
            close = df["Close"]
            if isinstance(close, pd.Series):
                close = close.to_frame(tickers[0])
            close.index = close.index.tz_localize(None) if close.index.tz is not None else close.index
            close.index = close.index.normalize()
        # One row per calendar day, last known close carried forward -> O(1) lookups per day
        table = {}
        if len(close.columns):
            days = pd.date_range(start, today, freq="D")
            close = close.reindex(close.index.union(days)).sort_index().ffill().reindex(days)
            table = {col: {ts.date(): (None if pd.isna(v) else float(v)) for ts, v in close[col].items()} for col in close.columns}

        def at(col, d):
            return table.get(col, {}).get(d)

        fx_at = lambda c, d: 1.0 if c == "HUF" else (at(f"{c}HUF=X", d) or get_huf_rate(c, d.isoformat()))
        # Every day from the first transaction to today (weekends repeat Friday's close)
        points = [start + timedelta(days=i) for i in range((today - start).days + 1)]

        dates, value, invested = [], [], []
        for p in points:
            pos, _, _, _ = compute(txs, prices, p, lambda s, p=p: ((at(s, p), None) if at(s, p) else None), fx_at)
            dates.append(p.isoformat())
            value.append(round(sum(x["value_huf"] for x in pos)))
            invested.append(round(sum(x["cost_huf"] for x in pos)))
        return {"dates": dates, "value": value, "invested": invested}
    return _cached(("history",), 3600, run)
