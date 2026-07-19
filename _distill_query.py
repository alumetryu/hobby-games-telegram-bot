import sqlite3, json, time, datetime

DB = r"C:\Users\Admin\.local\share\mimocode\mimocode.db"
conn = sqlite3.connect(DB)
cur = conn.cursor()

# 30 days ago in epoch ms
now = datetime.datetime(2026, 7, 19)
cutoff = now - datetime.timedelta(days=30)
cutoff_ms = int(cutoff.timestamp() * 1000)
print(f"Cutoff: {cutoff} = {cutoff_ms} ms")

# 1. List tables
print("=== TABLES ===")
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
for row in cur.fetchall():
    print(row)

# 2. Recent sessions (last 30 days = since 2026-06-19)
print("\n=== RECENT SESSIONS (last 30 days) ===")
cur.execute("""
    SELECT id, title, datetime(time_created/1000, 'unixepoch') as created
    FROM session
    WHERE time_created > ?
    ORDER BY time_created DESC
    LIMIT 50
""", (cutoff_ms,))
for row in cur.fetchall():
    print(row)

# 3. Schema of message and part tables
print("\n=== MESSAGE TABLE COLUMNS ===")
cur.execute("PRAGMA table_info(message)")
for row in cur.fetchall():
    print(row)

print("\n=== PART TABLE COLUMNS ===")
cur.execute("PRAGMA table_info(part)")
for row in cur.fetchall():
    print(row)

# 4. Top repeated tools in last 30 days
print("\n=== TOP REPEATED TOOLS (last 30 days) ===")
cur.execute("""
    SELECT json_extract(p.data, '$.tool') as tool,
           substr(json_extract(p.data, '$.state.input'), 1, 200) as input_preview,
           count(*) as n
    FROM message m
    JOIN part p ON p.message_id = m.id
    WHERE json_extract(m.data, '$.role') = 'assistant'
      AND json_extract(p.data, '$.type') = 'tool'
      AND m.time_created > ?
    GROUP BY tool, input_preview
    ORDER BY n DESC
    LIMIT 60
""", (cutoff_ms,))
for row in cur.fetchall():
    print(row)

conn.close()
