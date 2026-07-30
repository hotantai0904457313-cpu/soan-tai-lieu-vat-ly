"""
SQLite — lưu tài liệu, cài đặt, lịch sử AI.
"""
import sqlite3
import json
from contextlib import closing
from typing import List, Optional, Dict
from config import DB_PATH, DEFAULT_SETTINGS


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL: reader không chặn writer (SSE ghi ai_history trong lúc lưu tài liệu).
    # busy_timeout: chờ thay vì ném "database is locked" ngay.
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=5000')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def init_db():
    with closing(get_conn()) as conn:
        c = conn.cursor()

        c.execute('''CREATE TABLE IF NOT EXISTS documents (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            title        TEXT NOT NULL,
            subject      TEXT DEFAULT 'Vật lý',
            grade        INTEGER,
            chapter      TEXT,
            semester     TEXT,
            doc_type     TEXT,
            content_json TEXT,
            created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS ai_history (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id   INTEGER REFERENCES documents(id),
            question_id   TEXT,
            question_text TEXT,
            solution_text TEXT,
            model_used    TEXT,
            tokens_used   INTEGER,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )''')

        # Nạp cài đặt mặc định nếu chưa có
        for k, v in DEFAULT_SETTINGS.items():
            c.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (k, str(v)))

        conn.commit()


# ── Documents ─────────────────────────────────────────────────────

def save_document(doc_dict: Dict) -> int:
    content_json = json.dumps(doc_dict, ensure_ascii=False)
    with closing(get_conn()) as conn:
        c = conn.cursor()
        if doc_dict.get('id'):
            c.execute('''UPDATE documents
                         SET title=?, subject=?, grade=?, chapter=?, semester=?,
                             doc_type=?, content_json=?, updated_at=CURRENT_TIMESTAMP
                         WHERE id=?''',
                      (doc_dict['title'], doc_dict.get('subject','Vật lý'),
                       doc_dict.get('grade'), doc_dict.get('chapter',''),
                       doc_dict.get('semester',''), doc_dict.get('doc_type','khac'),
                       content_json, doc_dict['id']))
            doc_id = doc_dict['id']
        else:
            c.execute('''INSERT INTO documents (title, subject, grade, chapter, semester, doc_type, content_json)
                         VALUES (?, ?, ?, ?, ?, ?, ?)''',
                      (doc_dict['title'], doc_dict.get('subject','Vật lý'),
                       doc_dict.get('grade'), doc_dict.get('chapter',''),
                       doc_dict.get('semester',''), doc_dict.get('doc_type','khac'),
                       content_json))
            doc_id = c.lastrowid
        conn.commit()
        return doc_id


def get_document(doc_id: int) -> Optional[Dict]:
    with closing(get_conn()) as conn:
        row = conn.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d['content'] = json.loads(d['content_json']) if d['content_json'] else {}
    return d


def list_documents(grade=None, doc_type=None, search=None) -> List[Dict]:
    query = 'SELECT id, title, subject, grade, chapter, semester, doc_type, created_at, updated_at FROM documents WHERE 1=1'
    params = []
    if grade:
        query += ' AND grade=?'; params.append(grade)
    if doc_type:
        query += ' AND doc_type=?'; params.append(doc_type)
    if search:
        query += ' AND (title LIKE ? OR chapter LIKE ?)'; params += [f'%{search}%', f'%{search}%']
    query += ' ORDER BY updated_at DESC'
    with closing(get_conn()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def delete_document(doc_id: int):
    with closing(get_conn()) as conn:
        conn.execute('DELETE FROM ai_history WHERE document_id=?', (doc_id,))
        conn.execute('DELETE FROM documents WHERE id=?', (doc_id,))
        conn.commit()


# ── Settings ──────────────────────────────────────────────────────

def get_settings() -> Dict:
    with closing(get_conn()) as conn:
        rows = conn.execute('SELECT key, value FROM settings').fetchall()
    return {r['key']: r['value'] for r in rows}


def update_settings(data: Dict):
    with closing(get_conn()) as conn:
        for k, v in data.items():
            conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (k, str(v)))
        conn.commit()


# ── AI History ────────────────────────────────────────────────────

def save_ai_history(document_id, question_id, question_text, solution_text, model_used, tokens_used=0):
    with closing(get_conn()) as conn:
        conn.execute('''INSERT INTO ai_history
                        (document_id, question_id, question_text, solution_text, model_used, tokens_used)
                        VALUES (?, ?, ?, ?, ?, ?)''',
                     (document_id, question_id, question_text, solution_text, model_used, tokens_used))
        conn.commit()


def get_ai_history(document_id=None, limit=50) -> List[Dict]:
    with closing(get_conn()) as conn:
        if document_id:
            rows = conn.execute(
                'SELECT * FROM ai_history WHERE document_id=? ORDER BY created_at DESC LIMIT ?',
                (document_id, limit)).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM ai_history ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
    return [dict(r) for r in rows]


def delete_ai_history(history_id=None):
    with closing(get_conn()) as conn:
        if history_id:
            conn.execute('DELETE FROM ai_history WHERE id=?', (history_id,))
        else:
            conn.execute('DELETE FROM ai_history')
        conn.commit()
