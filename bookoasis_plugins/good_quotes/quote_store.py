# 글귀 콘텐츠를 코어 DB와 분리된 플러그인 전용 SQLite 파일에 원자적으로 저장합니다.
import json
from pathlib import Path
import sqlite3

DATA_PATH = Path(__file__).resolve().parent / 'data' / 'quotes.sqlite3'


class QuoteStore:
    def __init__(self, path=DATA_PATH):
        self.path = Path(path)
        self.snapshot = None

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            connection.execute('CREATE TABLE IF NOT EXISTS catalog (id INTEGER PRIMARY KEY CHECK (id = 1), payload TEXT NOT NULL)')
        except sqlite3.Error:
            connection.close()
            raise
        return connection

    def load(self):
        connection = self.connect()
        try:
            row = connection.execute('SELECT payload FROM catalog WHERE id = 1').fetchone()
            self.snapshot = row[0] if row else None
            return json.loads(row[0]) if row else {'quotes': [], 'photos': [], 'audit': []}
        finally:
            connection.close()

    def save(self, data):
        payload = json.dumps(data, ensure_ascii=False)
        connection = self.connect()
        try:
            with connection:
                if self.snapshot is None:
                    result = connection.execute('INSERT INTO catalog (id, payload) VALUES (1, ?) ON CONFLICT(id) DO NOTHING', (payload,))
                else:
                    result = connection.execute('UPDATE catalog SET payload = ? WHERE id = 1 AND payload = ?', (payload, self.snapshot))
                if result.rowcount != 1:
                    raise sqlite3.OperationalError('다른 요청이 글귀를 변경했습니다. 다시 불러온 뒤 실행해 주세요.')
            self.snapshot = payload
        finally:
            connection.close()
