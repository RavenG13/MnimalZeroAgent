import sqlite3, os

old = sqlite3.connect('memory.db')
rows = old.execute('SELECT id, user_id, time, content, tags FROM memories ORDER BY id').fetchall()
old.close()

print(f'总记录: {len(rows)}')

users = {}
for r in rows:
    uid = r[1]
    if uid not in users:
        users[uid] = []
    users[uid].append(r)

print('按用户:', {k: len(v) for k, v in users.items()})

for uid, records in users.items():
    safe = uid.replace('/', '_').replace('\\', '_').replace('.', '_')
    db_path = os.path.join('data', 'memory', f'{safe}.db')
    n = sqlite3.connect(db_path)
    n.execute('''CREATE TABLE IF NOT EXISTS memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time TEXT NOT NULL,
        content TEXT NOT NULL,
        tags TEXT DEFAULT ""
    )''')
    for r in records:
        n.execute('INSERT INTO memories (time, content, tags) VALUES (?, ?, ?)', (r[2], r[3], r[4]))
    n.commit()
    n.close()

print('迁移完成!')

for f in sorted(os.listdir('data/memory')):
    if f.endswith('.db'):
        c = sqlite3.connect(os.path.join('data', 'memory', f))
        cnt = c.execute('SELECT COUNT(*) FROM memories').fetchone()[0]
        print(f'  {f}: {cnt}条')
        c.close()
