"""
v6.0 SQLite 영구 장부 — holding_positions · trading_history · daily_snapshots.
v5.5.2 엔진 코어는 수정하지 않고, JSON 장부를 DB로 대체한다.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.live.live_account import LivePosition

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger("LiveDB")

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _now_kst_iso() -> str:
    return datetime.now(KST).isoformat()


def _to_db_date(value: str) -> str:
    """YYYY-MM-DD 또는 YYYYMMDD → YYYYMMDD."""
    raw = str(value).strip().replace("-", "")[:8]
    return raw


def _from_db_date(value: str) -> str:
    """YYYYMMDD → YYYY-MM-DD (LivePosition 호환)."""
    raw = str(value).strip().replace("-", "")[:8]
    if len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return str(value)[:10]


def default_db_path(project_root: str | Path | None = None) -> str:
    root = Path(project_root or Path(__file__).resolve().parents[2])
    return str(root / "data" / "live_trading.db")


def use_json_fallback() -> bool:
    return os.getenv("LIVE_USE_JSON_LEDGER", "").strip().lower() in ("1", "true", "yes")


@contextmanager
def _connect(db_path: str):
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(db_path: str | None = None, *, project_root: str | Path | None = None) -> str:
    """schema.sql 실행. 반환=실제 DB 경로."""
    path = db_path or default_db_path(project_root)
    ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
    with _connect(path) as conn:
        conn.executescript(ddl)
    logger.info("📦 SQLite 스키마 초기화 — %s", path)
    return path


def _row_to_position(row: sqlite3.Row) -> LivePosition:
    return LivePosition(
        code=str(row["symbol"]).zfill(6),
        qty=int(row["quantity"]),
        entry_price=float(row["entry_price"]),
        entry_date=_from_db_date(str(row["entry_date"])),
        hold_days=int(row["hold_days"]),
    )


def load_holding_positions(db_path: str) -> list[LivePosition]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT symbol, quantity, entry_price, entry_date, hold_days "
            "FROM holding_positions ORDER BY symbol"
        ).fetchall()
    return [_row_to_position(r) for r in rows]


def _load_position_names(db_path: str) -> dict[str, str]:
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT symbol, name FROM holding_positions").fetchall()
    return {str(r["symbol"]).zfill(6): str(r["name"] or "") for r in rows}


def save_holding_positions(
    db_path: str,
    positions: list[LivePosition],
    *,
    names: dict[str, str] | None = None,
) -> None:
    names = names or {}
    existing_names = _load_position_names(db_path) if not names else {}
    now = _now_kst_iso()
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM holding_positions")
        for pos in positions:
            c6 = str(pos.code).zfill(6)
            name = names.get(c6) or existing_names.get(c6) or ""
            conn.execute(
                """
                INSERT INTO holding_positions
                    (symbol, name, entry_date, entry_price, quantity, hold_days, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    c6,
                    name,
                    _to_db_date(pos.entry_date),
                    float(pos.entry_price),
                    int(pos.qty),
                    int(pos.hold_days),
                    now,
                ),
            )


def compute_profit_rate(
    *,
    entry_price: float,
    exit_price: float,
    quantity: int,
    buy_cost_ratio: float,
    sell_cost_ratio: float,
) -> float:
    cost_basis = entry_price * quantity * (1.0 + buy_cost_ratio)
    if cost_basis <= 0:
        return 0.0
    proceeds = exit_price * quantity * (1.0 - sell_cost_ratio)
    return proceeds / cost_basis - 1.0


def insert_trading_history(
    db_path: str,
    *,
    symbol: str,
    name: str,
    entry_date: str,
    entry_price: float,
    exit_date: str,
    exit_price: float,
    quantity: int,
    profit_rate: float,
    reason: str,
) -> int:
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO trading_history
                (symbol, name, entry_date, entry_price, exit_date, exit_price,
                 quantity, profit_rate, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(symbol).zfill(6),
                name or "",
                _to_db_date(entry_date),
                float(entry_price),
                _to_db_date(exit_date),
                float(exit_price),
                int(quantity),
                float(profit_rate),
                str(reason),
            ),
        )
        return int(cur.lastrowid or 0)


def upsert_daily_snapshot(
    db_path: str,
    *,
    base_date: str,
    available_cash: float,
    total_evaluation: float,
    total_asset: float,
) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO daily_snapshots
                (base_date, available_cash, total_evaluation, total_asset)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(base_date) DO UPDATE SET
                available_cash = excluded.available_cash,
                total_evaluation = excluded.total_evaluation,
                total_asset = excluded.total_asset
            """,
            (
                _to_db_date(base_date),
                float(available_cash),
                float(total_evaluation),
                float(total_asset),
            ),
        )


def _load_positions_from_json(json_path: str) -> list[LivePosition]:
    if not os.path.isfile(json_path):
        return []
    with open(json_path, encoding="utf-8") as fh:
        raw = json.load(fh)
    rows = raw.get("positions", raw) if isinstance(raw, dict) else raw
    out: list[LivePosition] = []
    for r in rows:
        out.append(
            LivePosition(
                code=str(r["code"]).zfill(6),
                qty=int(r["qty"]),
                entry_price=float(r["entry_price"]),
                entry_date=str(r["entry_date"])[:10],
                hold_days=int(r.get("hold_days", 0)),
            )
        )
    return out


def migrate_from_json(db_path: str, positions_json_path: str) -> int:
    """JSON 장부 → holding_positions 1회 이관. 반환=이관 건수."""
    positions = _load_positions_from_json(positions_json_path)
    if not positions:
        return 0
    save_holding_positions(db_path, positions)
    logger.info(
        "📥 JSON→DB 마이그레이션 완료 — %d종 (%s)",
        len(positions),
        positions_json_path,
    )
    return len(positions            )


def record_entry_ledger(
    db_path: str,
    pos: LivePosition,
    *,
    name: str = "",
) -> None:
    """매수 체결 SSOT — KIS sync 지연 시 entry_date 복원."""
    c6 = str(pos.code).zfill(6)
    now = _now_kst_iso()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO entry_ledger
                (symbol, name, entry_date, entry_price, quantity, hold_days, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                name = excluded.name,
                entry_date = excluded.entry_date,
                entry_price = excluded.entry_price,
                quantity = excluded.quantity,
                hold_days = excluded.hold_days,
                recorded_at = excluded.recorded_at
            """,
            (
                c6,
                name or "",
                _to_db_date(pos.entry_date),
                float(pos.entry_price),
                int(pos.qty),
                int(pos.hold_days),
                now,
            ),
        )


def lookup_entry_ledger(db_path: str, symbol: str) -> LivePosition | None:
    """entry_ledger에서 최신 매수 메타 조회."""
    c6 = str(symbol).zfill(6)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT symbol, entry_date, entry_price, quantity, hold_days
            FROM entry_ledger WHERE symbol = ?
            """,
            (c6,),
        ).fetchone()
    if row is None:
        return None
    return LivePosition(
        code=c6,
        qty=int(row["quantity"]),
        entry_price=float(row["entry_price"]),
        entry_date=_from_db_date(str(row["entry_date"])),
        hold_days=int(row["hold_days"]),
    )


def remove_entry_ledger(db_path: str, symbol: str) -> None:
    c6 = str(symbol).zfill(6)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM entry_ledger WHERE symbol = ?", (c6,))


def patch_holding_entry(
    db_path: str,
    symbol: str,
    *,
    entry_date: str,
    hold_days: int | None = None,
) -> bool:
    """holding_positions·entry_ledger entry_date/hold_days 일괄 보정."""
    c6 = str(symbol).zfill(6)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT quantity, entry_price, hold_days FROM holding_positions WHERE symbol = ?",
            (c6,),
        ).fetchone()
        if row is None:
            return False
        hd = int(row["hold_days"]) if hold_days is None else int(hold_days)
        conn.execute(
            "UPDATE holding_positions SET entry_date = ?, hold_days = ?, updated_at = ? WHERE symbol = ?",
            (_to_db_date(entry_date), hd, _now_kst_iso(), c6),
        )
        conn.execute(
            """
            INSERT INTO entry_ledger
                (symbol, name, entry_date, entry_price, quantity, hold_days, recorded_at)
            SELECT symbol, name, ?, entry_price, quantity, ?, ?
            FROM holding_positions WHERE symbol = ?
            ON CONFLICT(symbol) DO UPDATE SET
                entry_date = excluded.entry_date,
                hold_days = excluded.hold_days,
                recorded_at = excluded.recorded_at
            """,
            (_to_db_date(entry_date), hd, _now_kst_iso(), c6),
        )
    return True


def fetch_holding_rows(db_path: str) -> list[dict[str, object]]:
    """API용 — holding_positions 전체 행."""
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT symbol, name, entry_date, entry_price, quantity, hold_days, updated_at
            FROM holding_positions ORDER BY symbol
            """
        ).fetchall()
    out: list[dict[str, object]] = []
    for r in rows:
        out.append(
            {
                "symbol": str(r["symbol"]).zfill(6),
                "name": str(r["name"] or ""),
                "entry_date": _from_db_date(str(r["entry_date"])),
                "entry_price": float(r["entry_price"]),
                "quantity": int(r["quantity"]),
                "hold_days": int(r["hold_days"]),
                "updated_at": str(r["updated_at"]),
            }
        )
    return out


def fetch_trading_history(
    db_path: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, object]]:
    sql = (
        "SELECT id, symbol, name, entry_date, entry_price, exit_date, exit_price, "
        "quantity, profit_rate, reason FROM trading_history WHERE 1=1"
    )
    params: list[object] = []
    if date_from:
        sql += " AND exit_date >= ?"
        params.append(_to_db_date(date_from))
    if date_to:
        sql += " AND exit_date <= ?"
        params.append(_to_db_date(date_to))
    sql += " ORDER BY exit_date DESC, id DESC"
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    out: list[dict[str, object]] = []
    for r in rows:
        out.append(
            {
                "id": int(r["id"]),
                "symbol": str(r["symbol"]).zfill(6),
                "name": str(r["name"] or ""),
                "entry_date": _from_db_date(str(r["entry_date"])),
                "entry_price": float(r["entry_price"]),
                "exit_date": _from_db_date(str(r["exit_date"])),
                "exit_price": float(r["exit_price"]),
                "quantity": int(r["quantity"]),
                "profit_rate": float(r["profit_rate"]),
                "reason": str(r["reason"]),
            }
        )
    return out


def fetch_daily_snapshots(
    db_path: str,
    *,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, object]]:
    sql = (
        "SELECT base_date, available_cash, total_evaluation, total_asset "
        "FROM daily_snapshots WHERE 1=1"
    )
    params: list[object] = []
    if date_from:
        sql += " AND base_date >= ?"
        params.append(_to_db_date(date_from))
    if date_to:
        sql += " AND base_date <= ?"
        params.append(_to_db_date(date_to))
    sql += " ORDER BY base_date ASC"
    with _connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "base_date": _from_db_date(str(r["base_date"])),
            "available_cash": float(r["available_cash"]),
            "total_evaluation": float(r["total_evaluation"]),
            "total_asset": float(r["total_asset"]),
        }
        for r in rows
    ]


def fetch_trading_summary(db_path: str, *, today: str | None = None) -> dict[str, object]:
    """한투 미제공 통계 — trading_history 자체 연산 (승률·누적·오늘 실현)."""
    today_db = _to_db_date(today or datetime.now(KST).strftime("%Y-%m-%d"))
    with _connect(db_path) as conn:
        all_rows = conn.execute(
            "SELECT profit_rate, exit_date FROM trading_history"
        ).fetchall()

    total = len(all_rows)
    if not total:
        return {
            "source": "db",
            "total_trades": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "total_profit_rate": 0.0,
            "today_trade_count": 0,
            "today_profit_rate": 0.0,
            "cumulative_profit_rate": 0.0,
            "today_realized_profit_rate": 0.0,
        }

    wins = sum(1 for r in all_rows if float(r["profit_rate"]) > 0)
    losses = total - wins
    cumulative = sum(float(r["profit_rate"]) for r in all_rows)
    today_rows = [r for r in all_rows if str(r["exit_date"]) == today_db]
    today_pnl = sum(float(r["profit_rate"]) for r in today_rows)

    return {
        "source": "db",
        "total_trades": total,
        "win_count": wins,
        "loss_count": losses,
        "win_rate": round(wins / total, 4),
        "total_profit_rate": round(cumulative, 4),
        "today_trade_count": len(today_rows),
        "today_profit_rate": round(today_pnl, 4),
        "cumulative_profit_rate": round(cumulative, 4),
        "today_realized_profit_rate": round(today_pnl, 4),
    }


DEFAULT_RESET_CASH = 10_000_000.0


def replace_universe_candidates(
    db_path: str,
    *,
    scan_date: str,
    scan_time: str,
    items: list[dict[str, object]],
) -> int:
    """당일 스캔 후보 종목을 universe_candidates에 전면 교체. 반환=저장 건수."""
    scan_d = _to_db_date(scan_date)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM universe_candidates")
        for item in items:
            conn.execute(
                """
                INSERT INTO universe_candidates
                    (scan_date, rank, symbol, name, market_cap, volume_amt, scan_time)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_d,
                    int(item.get("rank") or 0),
                    str(item.get("code", "")).zfill(6),
                    str(item.get("name", "")),
                    str(item.get("market_cap") or ""),
                    str(item.get("volume_amt") or ""),
                    scan_time,
                ),
            )
    return len(items)


def persist_universe_candidates_from_meta(db_path: str, meta_path: str) -> int:
    """live_today_universe.meta.json → universe_candidates 동기화."""
    if use_json_fallback() or not os.path.isfile(meta_path):
        return 0
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    items = meta.get("scanned_items_report") or []
    return replace_universe_candidates(
        db_path,
        scan_date=str(meta.get("base_date_actual") or ""),
        scan_time=str(meta.get("scan_time") or _now_kst_iso()),
        items=items,
    )


def reset_universe_files(universe_json_path: str, universe_meta_path: str) -> None:
    """당일 유니버스 JSON을 []로 초기화하고 메타 리포트 파일을 삭제."""
    parent = os.path.dirname(universe_json_path) or "."
    os.makedirs(parent, exist_ok=True)
    with open(universe_json_path, "w", encoding="utf-8") as fh:
        json.dump([], fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    logger.info("📁 live_today_universe.json 후보 파일 초기화 완료 — %s", universe_json_path)
    if os.path.isfile(universe_meta_path):
        os.remove(universe_meta_path)
        logger.info("📋 live_today_universe.meta.json 메타 파일 삭제 완료 — %s", universe_meta_path)


def reset_system_database(
    db_path: str,
    *,
    initial_cash: float = DEFAULT_RESET_CASH,
    universe_json_path: str | None = None,
    universe_meta_path: str | None = None,
) -> str:
    """
    holding_positions · trading_history · daily_snapshots · universe_candidates 전면 삭제 후
    VACUUM · 원금 스냅샷 1건 박제 · 유니버스 JSON/메타 파일 세척.
    반환=base_date (YYYYMMDD).
    """
    init_schema(db_path)
    today = _to_db_date(datetime.now(KST).strftime("%Y-%m-%d"))
    cash = float(initial_cash)
    with _connect(db_path) as conn:
        conn.execute("DELETE FROM holding_positions")
        conn.execute("DELETE FROM trading_history")
        conn.execute("DELETE FROM daily_snapshots")
        conn.execute("DELETE FROM universe_candidates")
        conn.execute(
            """
            INSERT INTO daily_snapshots
                (base_date, available_cash, total_evaluation, total_asset)
            VALUES (?, ?, ?, ?)
            """,
            (today, cash, 0.0, cash),
        )
    if universe_json_path and universe_meta_path:
        reset_universe_files(universe_json_path, universe_meta_path)
    vac = sqlite3.connect(db_path)
    try:
        vac.execute("VACUUM")
        vac.commit()
    finally:
        vac.close()
    logger.critical(
        "🔥 [DB 전면 초기화] 유니버스 후보 포함 · 원금 %s원 스냅샷 박제 — %s",
        f"{cash:,.0f}",
        db_path,
    )
    return today


def ensure_db_ready(
    project_root: str | Path,
    positions_json_path: str | None = None,
) -> str:
    """
    DB 파일·스키마 보장. holding_positions 비어 있고 JSON에 데이터 있으면 자동 이관.
    반환=db_path.
    """
    db_path = default_db_path(project_root)
    init_schema(db_path)

    with _connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM holding_positions").fetchone()[0]

    if count == 0 and positions_json_path:
        json_positions = _load_positions_from_json(positions_json_path)
        if json_positions:
            migrate_from_json(db_path, positions_json_path)

    return db_path
