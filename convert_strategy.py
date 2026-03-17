#!/usr/bin/env python3
"""Convert strategy txt files to SQLite for fast lookups."""

import sqlite3
import re
import sys
import time

BATCH_SIZE = 50000

def parse_and_insert(strategy_file, db_file):
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("DROP TABLE IF EXISTS strategy")
    conn.execute("""
        CREATE TABLE strategy (
            key TEXT PRIMARY KEY,
            actions TEXT
        )
    """)

    header_re = re.compile(r'^(P[01]) \[([^\]]+)\] \(p=[^)]+\) \| (.+)$')
    action_re = re.compile(r'^\s+(\S+):\s+(\S+)$')

    batch = []
    current_key = None
    current_actions = []
    count = 0
    start = time.time()

    with open(strategy_file, 'r') as f:
        for line in f:
            line = line.rstrip('\n')

            if line.startswith('#') or line == '':
                if current_key and current_actions:
                    actions_str = '|'.join(f'{a}:{p}' for a, p in current_actions)
                    batch.append((current_key, actions_str))
                    count += 1
                    if len(batch) >= BATCH_SIZE:
                        conn.executemany("INSERT INTO strategy VALUES (?, ?)", batch)
                        batch = []
                        elapsed = time.time() - start
                        print(f"\r  {count:,} info sets ({elapsed:.0f}s)", end='', flush=True)
                    current_key = None
                    current_actions = []
                continue

            m = header_re.match(line)
            if m:
                if current_key and current_actions:
                    actions_str = '|'.join(f'{a}:{p}' for a, p in current_actions)
                    batch.append((current_key, actions_str))
                    count += 1
                    if len(batch) >= BATCH_SIZE:
                        conn.executemany("INSERT INTO strategy VALUES (?, ?)", batch)
                        batch = []
                        elapsed = time.time() - start
                        print(f"\r  {count:,} info sets ({elapsed:.0f}s)", end='', flush=True)

                player = m.group(1)
                hand = m.group(2).replace(' ', '')
                history = m.group(3).strip()
                current_key = f"{player}|{hand}|{history}"
                current_actions = []
                continue

            m = action_re.match(line)
            if m:
                current_actions.append((m.group(1), m.group(2)))

    # flush remaining
    if current_key and current_actions:
        actions_str = '|'.join(f'{a}:{p}' for a, p in current_actions)
        batch.append((current_key, actions_str))
        count += 1
    if batch:
        conn.executemany("INSERT INTO strategy VALUES (?, ?)", batch)

    print(f"\r  {count:,} info sets total")
    print("Creating index...")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_key ON strategy(key)")
    conn.commit()
    conn.close()
    elapsed = time.time() - start
    print(f"Done! {db_file} created in {elapsed:.0f}s")

if __name__ == '__main__':
    files = [
        ("strategy_1v1.txt", "strategy_1v1.db"),
        ("strategy_2v2.txt", "strategy_2v2.db"),
    ]
    import os
    for txt, db in files:
        if os.path.exists(txt) and not os.path.exists(db):
            print(f"Converting {txt} -> {db}...")
            parse_and_insert(txt, db)
        elif os.path.exists(txt) and os.path.exists(db):
            print(f"Skipping {txt} ({db} already exists)")
        else:
            print(f"Skipping {txt} (not found)")
