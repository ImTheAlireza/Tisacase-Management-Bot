"""Exercise SQL parameter binding and JSON round trips with a local SQLite DB.

The deployed schema/migration is MySQL-specific; these tests don't replace the
MySQL integration suite, but need no external DB and catch persistence regressions.
"""
import sqlite3
from unittest.mock import patch

from models.design import Design
from utils.enums import DesignStatus


class SQLiteCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, sql, params=()):
        return self.cursor.execute(sql.replace('%s', '?'), params)

    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row is not None else None

    @property
    def rowcount(self):
        return self.cursor.rowcount

    @property
    def lastrowid(self):
        return self.cursor.lastrowid

    def close(self):
        self.cursor.close()


class SQLiteConnection:
    def __init__(self, db_path):
        self.connection = sqlite3.connect(db_path)
        self.connection.row_factory = sqlite3.Row

    def cursor(self, *args):
        return SQLiteCursor(self.connection.cursor())

    def begin(self):
        self.connection.execute('BEGIN')

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


def test_save_load_edit_reject_and_reuse_code_with_metadata(tmp_path):
    db_path = str(tmp_path / 'designs.sqlite')
    connection = sqlite3.connect(db_path)
    connection.execute('''
        CREATE TABLE designs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            product_line_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            editor_user_id INTEGER,
            editor_name TEXT,
            reviewer_user_id INTEGER,
            reviewer_name TEXT,
            mockup_file_ids TEXT,
            print_file_ids TEXT,
            mockup_message_ids_reviewer TEXT,
            file_types TEXT,
            print_file_metadata TEXT,
            created_at TEXT,
            reviewed_at TEXT,
            final_name TEXT,
            metadata TEXT
        )
    ''')
    connection.close()
    metadata = {'p1': {'file_name': 'طرح چاپی.PSD', 'mime_type': 'image/psd'}}
    with patch('models.design.get_db_connection', side_effect=lambda: SQLiteConnection(db_path)):
        design = Design(
            id=None, code='TS350', product_line_id=3, editor_user_id=1001,
            editor_name='Ali', mockup_file_ids=['m1'], print_file_ids=['p1'],
            file_types={'m1': 'photo', 'p1': 'document'}, print_file_metadata=metadata,
        )
        design.save()
        assert design.id is not None
        loaded = Design.get_by_code('TS350')
        assert loaded.print_file_metadata == metadata
        assert loaded.print_file_ids == ['p1']

        loaded.print_file_metadata['p1']['file_name'] = 'نسخه اصلاح‌شده.psb'
        loaded.save()
        reloaded = Design.get_by_code('TS350')
        assert reloaded.print_file_metadata['p1']['file_name'] == 'نسخه اصلاح‌شده.psb'
        assert reloaded.reject(2001, 'Nazi') is True
        assert reloaded.status == DesignStatus.REJECTED
        assert Design.get_by_code('TS350') is None

        new_design = Design(id=None, code='TS350', product_line_id=3, editor_user_id=1001)
        new_design.save()
        assert Design.get_by_code('TS350').print_file_metadata == {}

    connection = sqlite3.connect(db_path)
    rejected = connection.execute(
        "SELECT status, print_file_metadata FROM designs WHERE code LIKE 'TS350_REJ_%'"
    ).fetchone()
    connection.close()
    assert rejected[0] == 'rejected'
    assert 'نسخه اصلاح‌شده.psb' in rejected[1]
