import sqlite3, json

DB = r"C:\Users\Admin\.local\share\mimocode\mimocode.db"
conn = sqlite3.connect(DB)
cur = conn.cursor()

# Full content of the deployment session (ses_086034c40ffenTZMok48r51qJR)
print("=== ses_086034c40ffenTZMok48r51qJR: Telegram-bothost.ru (all parts) ===")
cur.execute("""SELECT m.id, json_extract(m.data, '$.role') as role
               FROM message m WHERE m.session_id = 'ses_086034c40ffenTZMok48r51qJR'
               ORDER BY m.time_created""")
msgs = cur.fetchall()
for mid, role in msgs:
    print(f"\n--- {role} ({mid[:25]}) ---")
    cur2 = conn.cursor()
    # text parts
    cur2.execute("""SELECT json_extract(p.data, '$.text')
                    FROM part p WHERE p.message_id = ? AND json_extract(p.data, '$.type') = 'text'
                    ORDER BY p.time_created""", (mid,))
    for (txt,) in cur2.fetchall():
        if txt:
            print(txt[:1000])
    # tool parts
    cur2.execute("""SELECT json_extract(p.data, '$.tool'),
                           json_extract(p.data, '$.state.input'),
                           substr(json_extract(p.data, '$.state.output'), 1, 500)
                    FROM part p WHERE p.message_id = ? AND json_extract(p.data, '$.type') = 'tool'
                    ORDER BY p.time_created""", (mid,))
    for tool, inp, out in cur2.fetchall():
        if tool:
            print(f"  [TOOL: {tool}]")
            if inp:
                print(f"    INPUT: {inp[:300]}")
            if out:
                print(f"    OUTPUT: {out[:400]}")

# Also check: what's in the Auto Distill session (ses_086034b2dffeU8IOiMK4s7MFie)
print("\n\n=== ses_086034b2dffeU8IOiMK4s7MFie: Auto Distill (all parts) ===")
cur.execute("""SELECT m.id, json_extract(m.data, '$.role') as role
               FROM message m WHERE m.session_id = 'ses_086034b2dffeU8IOiMK4s7MFie'
               ORDER BY m.time_created""")
msgs = cur.fetchall()
for mid, role in msgs:
    print(f"\n--- {role} ({mid[:25]}) ---")
    cur2 = conn.cursor()
    cur2.execute("""SELECT json_extract(p.data, '$.text')
                    FROM part p WHERE p.message_id = ? AND json_extract(p.data, '$.type') = 'text'
                    ORDER BY p.time_created""", (mid,))
    for (txt,) in cur2.fetchall():
        if txt:
            print(txt[:1000])
    cur2.execute("""SELECT json_extract(p.data, '$.tool'),
                           json_extract(p.data, '$.state.input'),
                           substr(json_extract(p.data, '$.state.output'), 1, 500)
                    FROM part p WHERE p.message_id = ? AND json_extract(p.data, '$.type') = 'tool'
                    ORDER BY p.time_created""", (mid,))
    for tool, inp, out in cur2.fetchall():
        if tool:
            print(f"  [TOOL: {tool}]")
            if inp:
                print(f"    INPUT: {inp[:300]}")
            if out:
                print(f"    OUTPUT: {out[:400]}")

conn.close()
