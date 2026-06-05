-- v6.0 라이브 매매 SQLite 스키마 (DDL 정본)
-- 적용: live_db.init_schema() 가 본 파일을 실행한다.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS holding_positions (
    symbol       TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    entry_date   TEXT NOT NULL,
    entry_price  REAL NOT NULL CHECK (entry_price > 0),
    quantity     INTEGER NOT NULL CHECK (quantity > 0),
    hold_days    INTEGER NOT NULL DEFAULT 0 CHECK (hold_days >= 0),
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trading_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT NOT NULL,
    name         TEXT NOT NULL DEFAULT '',
    entry_date   TEXT NOT NULL,
    entry_price  REAL NOT NULL,
    exit_date    TEXT NOT NULL,
    exit_price   REAL NOT NULL,
    quantity     INTEGER NOT NULL,
    profit_rate  REAL NOT NULL,
    reason       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trading_history_exit_date ON trading_history(exit_date);
CREATE INDEX IF NOT EXISTS idx_trading_history_symbol ON trading_history(symbol);

CREATE TABLE IF NOT EXISTS daily_snapshots (
    base_date          TEXT PRIMARY KEY,
    available_cash     REAL NOT NULL,
    total_evaluation   REAL NOT NULL,
    total_asset        REAL NOT NULL CHECK (total_asset >= 0)
);
