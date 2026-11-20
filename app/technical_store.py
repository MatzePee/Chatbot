"""Minimal installation store schema: settings and owner-neutral audit only."""
AUDIT_DDL = '''CREATE TABLE IF NOT EXISTS logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, level TEXT,
 category TEXT, message TEXT, detail TEXT)'''

def initialize(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
    conn.execute(AUDIT_DDL)
    conn.execute('CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts DESC)')
