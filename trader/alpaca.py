"""
trader/alpaca.py
Thin wrapper around the Alpaca REST API (alpaca-py SDK).

Reads TRADING_MODE from config/guardrails.md to decide paper vs live URL.
All keys from environment variables — never hardcoded.
"""

from __future__ import annotations

import os
import re
import time
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    GetOrdersRequest,
)
from alpaca.trading.enums import (
    OrderSide,
    TimeInForce,
    OrderClass,
    OrderStatus,
    QueryOrderStatus,
)
from alpaca.trading.models import Order, Position, TradeAccount
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GUARDRAILS = os.path.join(_BASE, "config", "guardrails.md")


def _is_paper_mode() -> bool:
    try:
        with open(_GUARDRAILS) as f:
            text = f.read()
        match = re.search(r"TRADING_MODE\s*=\s*(\w+)", text)
        if match:
            return match.group(1).strip().upper() != "LIVE"
    except FileNotFoundError:
        pass
    return True  # default to paper if config missing


class AlpacaClient:
    """Wraps TradingClient with helpers for day trading operations."""

    def __init__(self):
        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret  = os.environ.get("ALPACA_SECRET_KEY", "")
        paper   = _is_paper_mode()

        if not api_key or not secret:
            raise EnvironmentError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set as environment variables."
            )

        self.paper  = paper
        self.client = TradingClient(api_key, secret, paper=paper)
        self.data_client = StockHistoricalDataClient(api_key, secret)
        mode_str = "PAPER" if paper else "🔴 LIVE"
        print(f"  🔌 Alpaca connected [{mode_str}]")

    # ── Account ────────────────────────────────────────────────────────────────

    def get_account(self) -> TradeAccount:
        return self.client.get_account()

    def get_equity(self) -> float:
        return float(self.client.get_account().equity)

    def get_buying_power(self) -> float:
        return float(self.client.get_account().buying_power)

    # ── Quotes ─────────────────────────────────────────────────────────────────

    def get_latest_price(self, ticker: str) -> Optional[float]:
        try:
            req = StockLatestQuoteRequest(symbol_or_symbols=ticker)
            quote = self.data_client.get_stock_latest_quote(req)
            q = quote[ticker]
            mid = (float(q.ask_price) + float(q.bid_price)) / 2
            return round(mid, 2)
        except Exception:
            return None

    # ── Orders ─────────────────────────────────────────────────────────────────

    def place_bracket_order(
        self,
        ticker:          str,
        qty:             int,
        limit_price:     float,
        stop_price:      float,
        take_profit_price: float,
    ) -> Optional[Order]:
        """
        Bracket order: entry limit + auto stop-loss + auto take-profit.
        All three legs submitted in one API call.
        """
        from alpaca.trading.requests import TakeProfitRequest, StopLossRequest

        req = LimitOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2),
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=round(take_profit_price, 2)),
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
        )
        try:
            order = self.client.submit_order(req)
            return order
        except Exception as e:
            print(f"  ❌ Order failed for {ticker}: {e}")
            return None

    def place_market_order(
        self, ticker: str, qty: int, side: str = "buy"
    ) -> Optional[Order]:
        req = MarketOrderRequest(
            symbol=ticker,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        try:
            return self.client.submit_order(req)
        except Exception as e:
            print(f"  ❌ Market order failed for {ticker}: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        try:
            self.client.cancel_order_by_id(order_id)
            return True
        except Exception:
            return False

    def cancel_all_orders(self) -> int:
        cancelled = self.client.cancel_orders()
        return len(cancelled)

    # ── Positions ──────────────────────────────────────────────────────────────

    def get_positions(self) -> list[Position]:
        return self.client.get_all_positions()

    def get_position(self, ticker: str) -> Optional[Position]:
        try:
            return self.client.get_open_position(ticker)
        except Exception:
            return None

    def close_position(self, ticker: str) -> bool:
        try:
            self.client.close_position(ticker)
            return True
        except Exception as e:
            print(f"  ⚠ Could not close {ticker}: {e}")
            return False

    def close_all_positions(self) -> list[str]:
        """Close every open position. Returns list of closed tickers."""
        positions = self.get_positions()
        closed = []
        for pos in positions:
            if self.close_position(pos.symbol):
                closed.append(pos.symbol)
                time.sleep(0.2)
        return closed

    # ── Orders query ──────────────────────────────────────────────────────────

    def get_open_orders(self) -> list[Order]:
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        return self.client.get_orders(req)

    def get_order(self, order_id: str) -> Optional[Order]:
        try:
            return self.client.get_order_by_id(order_id)
        except Exception:
            return None
