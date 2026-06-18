"""
v11.2 라이브 ORB 모의투자 SQLite 적재소.

data/live_trading.db — live_equity · live_trades 테이블
"""
from __future__ import annotations

import csv
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger("LiveDbManager")
KST = ZoneInfo("Asia/Seoul")

V11_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_equity (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT NOT NULL,
    total_equity  REAL NOT NULL,
    safe_vault    REAL NOT NULL DEFAULT 0,
    used_cash     REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_live_equity_ts ON live_equity(timestamp);

CREATE TABLE IF NOT EXISTS live_trades (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_time   TEXT NOT NULL,
    exit_time    TEXT NOT NULL,
    code         TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    buy_price    REAL NOT NULL,
    sell_price   REAL NOT NULL,
    qty          INTEGER NOT NULL DEFAULT 0,
    pnl_rate     REAL NOT NULL,
    exit_reason  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_live_trades_exit ON live_trades(exit_time);
CREATE INDEX IF NOT EXISTS idx_live_trades_code ON live_trades(code);
"""


def default_db_path(project_root: str | Path | None = None) -> Path:
    root = Path(project_root or Path(__file__).resolve().parents[2])
    return root / "data" / "live_trading.db"


@contextmanager
def _connect(db_path: str | Path, *, ensure_schema: bool = True):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        if ensure_schema:
            conn.executescript(V11_SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_v11_schema(db_path: str | Path | None = None, *, project_root: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else default_db_path(project_root)
    with _connect(path) as conn:
        conn.executescript(V11_SCHEMA)
    logger.info("📦 v11.2 DB 스키마 준비 — %s", path)
    return path


class LiveDbManager:
    """v11.2 live_equity · live_trades CRUD."""

    def __init__(self, db_path: str | Path | None = None, *, project_root: str | Path | None = None):
        self.db_path = init_v11_schema(db_path, project_root=project_root)

    def insert_equity_snapshot(
        self,
        *,
        total_equity: float,
        safe_vault: float,
        used_cash: float,
        timestamp: str | None = None,
    ) -> None:
        ts = timestamp or datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        with _connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO live_equity (timestamp, total_equity, safe_vault, used_cash)
                VALUES (?, ?, ?, ?)
                """,
                (ts, float(total_equity), float(safe_vault), float(used_cash)),
            )

    def insert_trade(
        self,
        *,
        entry_time: str,
        exit_time: str,
        code: str,
        name: str,
        buy_price: float,
        sell_price: float,
        qty: int,
        pnl_rate: float,
        exit_reason: str,
    ) -> int:
        with _connect(self.db_path) as conn:
            cur = conn.execute(
                """
                INSERT INTO live_trades
                    (entry_time, exit_time, code, name, buy_price, sell_price, qty, pnl_rate, exit_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_time,
                    exit_time,
                    str(code).zfill(6),
                    name or "",
                    float(buy_price),
                    float(sell_price),
                    int(qty),
                    float(pnl_rate),
                    str(exit_reason),
                ),
            )
            return int(cur.lastrowid or 0)

    def fetch_equity_today(self, *, date_prefix: str | None = None) -> list[dict]:
        prefix = date_prefix or datetime.now(KST).strftime("%Y-%m-%d")
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT timestamp, total_equity, safe_vault, used_cash
                FROM live_equity WHERE timestamp LIKE ?
                ORDER BY timestamp ASC
                """,
                (f"{prefix}%",),
            ).fetchall()
        return [dict(r) for r in rows]

    def fetch_trades_today(self, *, date_prefix: str | None = None) -> list[dict]:
        prefix = date_prefix or datetime.now(KST).strftime("%Y-%m-%d")
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT entry_time, exit_time, code, name, buy_price, sell_price,
                       qty, pnl_rate, exit_reason
                FROM live_trades WHERE exit_time LIKE ?
                ORDER BY exit_time ASC
                """,
                (f"{prefix}%",),
            ).fetchall()
        return [dict(r) for r in rows]

    def fetch_latest_equity(self) -> dict | None:
        with _connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT timestamp, total_equity, safe_vault, used_cash
                FROM live_equity ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
        return dict(row) if row else None

    def today_realized_pnl_rate(self, *, date_prefix: str | None = None) -> float:
        trades = self.fetch_trades_today(date_prefix=date_prefix)
        return sum(float(t["pnl_rate"]) for t in trades)


def dump_eod_reports(
    db: LiveDbManager,
    *,
    trade_date: str | None = None,
    output_dir: str | Path | None = None,
    project_root: str | Path | None = None,
    safe_vault: float = 0.0,
    total_equity: float = 0.0,
    rebalance_event: str = "none",
) -> tuple[Path, Path]:
    """15:30 EOD — CSV + MD 장부 덤프."""
    root = Path(project_root or Path(__file__).resolve().parents[2])
    out = Path(output_dir) if output_dir else root / "outputs"
    out.mkdir(parents=True, exist_ok=True)

    day = trade_date or datetime.now(KST).strftime("%Y-%m-%d")
    ymd = day.replace("-", "")
    csv_path = out / f"v11_live_trades_{ymd}.csv"
    md_path = out / f"v11_live_report_{ymd}.md"

    trades = db.fetch_trades_today(date_prefix=day)
    equity_rows = db.fetch_equity_today(date_prefix=day)

    fields = [
        "entry_time", "exit_time", "code", "name",
        "buy_price", "sell_price", "qty", "pnl_rate", "exit_reason",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for t in trades:
            w.writerow(t)

    wins = sum(1 for t in trades if float(t["pnl_rate"]) > 0)
    total_pnl = sum(float(t["pnl_rate"]) for t in trades)
    open_eq = equity_rows[0]["total_equity"] if equity_rows else total_equity
    close_eq = equity_rows[-1]["total_equity"] if equity_rows else total_equity

    lines = [
        f"# v11.2 라이브 모의투자 일일 리포트 — {day}",
        "",
        "## 계좌 요약",
        f"| 항목 | 값 |",
        f"|------|-----|",
        f"| 장 시작 추정 자산 | {open_eq:,.0f}원 |",
        f"| 장 마감 총자산 | {close_eq:,.0f}원 |",
        f"| Safe Vault 금고 | {safe_vault:,.0f}원 |",
        f"| EOD 이벤트 | {rebalance_event} |",
        "",
        "## 매매 실적",
        f"- 체결 건수: **{len(trades)}**",
        f"- 승률: **{wins}/{len(trades)}** ({(wins/len(trades)*100 if trades else 0):.1f}%)",
        f"- 합산 수익률: **{total_pnl*100:+.2f}%**",
        "",
    ]
    if trades:
        lines.append("## 거래 내역")
        lines.append("| 종목 | 매수 | 매도 | 수익률 | 사유 |")
        lines.append("|------|------|------|--------|------|")
        for t in trades:
            lines.append(
                f"| {t['name']} ({t['code']}) | {float(t['buy_price']):,.0f} | "
                f"{float(t['sell_price']):,.0f} | {float(t['pnl_rate'])*100:+.2f}% | {t['exit_reason']} |"
            )
    else:
        lines.append("_오늘 체결된 거래 없음._")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("📜 EOD 장부 저장 — %s, %s", csv_path.name, md_path.name)
    return csv_path, md_path
