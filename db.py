import os
import sqlite3

DB_PATH = os.environ.get("DIAZBETS_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "diazbets.sqlite"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    pw_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_usage (
    day TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fixtures (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    kickoff TEXT NOT NULL,
    league_id INTEGER NOT NULL,
    league_name TEXT,
    country TEXT,
    season INTEGER,
    home_id INTEGER, home TEXT, home_logo TEXT,
    away_id INTEGER, away TEXT, away_logo TEXT,
    status TEXT,
    home_goals INTEGER,
    away_goals INTEGER
);
CREATE INDEX IF NOT EXISTS idx_fixtures_day ON fixtures(day);
CREATE TABLE IF NOT EXISTS odds (
    fixture_id INTEGER NOT NULL,
    market TEXT NOT NULL,
    selection TEXT NOT NULL,
    price REAL NOT NULL,
    PRIMARY KEY (fixture_id, market, selection)
);
CREATE TABLE IF NOT EXISTS injuries (
    fixture_id INTEGER NOT NULL,
    team_id INTEGER,
    team TEXT,
    player TEXT,
    type TEXT,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_injuries_fixture ON injuries(fixture_id);
CREATE TABLE IF NOT EXISTS h2h (
    fixture_id INTEGER PRIMARY KEY,
    json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS standings (
    league_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    fetched_day TEXT NOT NULL,
    json TEXT NOT NULL,
    PRIMARY KEY (league_id, season)
);
CREATE TABLE IF NOT EXISTS fetched_days (
    day TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS tips (
    fixture_id INTEGER NOT NULL,
    market TEXT NOT NULL,
    pick TEXT NOT NULL,
    prob REAL NOT NULL,
    odds REAL,
    value REAL,
    note TEXT,
    result TEXT,
    PRIMARY KEY (fixture_id, market)
);
"""


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
