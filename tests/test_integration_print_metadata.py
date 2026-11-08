"""MySQL-backed tests; use TEST_DB_NAME and never the production database."""
from unittest.mock import patch

import pytest

from models.design import Design
from tests.conftest import get_test_connection
from utils.enums import DesignStatus


@pytest.fixture(autouse=True)
def use_test_db(test_db):
    with patch('models.design.get_db_connection', side_effect=get_test_connection):
        yield


def test_mysql_saves_and_recovers_original_filenames(seed_product_line, seed_editor):
    metadata = {'print1': {'file_name': 'طرح.PSD', 'mime_type': 'image/psd'}}
    design = Design(
        id=None, code='TS350', product_line_id=seed_product_line,
        editor_user_id=seed_editor, editor_name='Ali',
        mockup_file_ids=['mockup1'], print_file_ids=['print1'],
        print_file_metadata=metadata,
    )
    design.save()
    loaded = Design.get_by_code('TS350')
    assert loaded.print_file_metadata == metadata
    loaded.print_file_metadata['print1']['file_name'] = 'اصلاح‌شده.PSB'
    loaded.save()
    assert Design.get_by_code('TS350').print_file_metadata['print1']['file_name'] == 'اصلاح‌شده.PSB'
    assert loaded.reject(2001, 'Nazi')
    assert loaded.status == DesignStatus.REJECTED


def test_mysql_old_row_without_metadata_is_still_readable(seed_product_line, seed_editor, db_conn):
    cursor = db_conn.cursor()
    cursor.execute('''
        INSERT INTO designs (code, product_line_id, status, editor_user_id,
                             mockup_file_ids, print_file_ids)
        VALUES (%s, %s, 'pending', %s, '[]', '["print1"]')
    ''', ('TS351', seed_product_line, seed_editor))
    db_conn.commit()
    cursor.close()
    loaded = Design.get_by_code('TS351')
    assert loaded.print_file_ids == ['print1']
    assert loaded.print_file_metadata == {}


def test_mysql_migration_is_idempotent_when_column_exists(db_conn):
    from migrations.migration_011_add_print_file_metadata import Migration011

    cursor = db_conn.cursor()
    try:
        Migration011.up(cursor)
        cursor.execute("SHOW COLUMNS FROM designs LIKE 'print_file_metadata'")
        assert cursor.fetchone() is not None
    finally:
        cursor.close()


def test_mysql_rejection_with_long_original_code(seed_product_line, seed_editor, db_conn):
    code = 'LONGPREFIX123'  # a legal 10-character prefix plus a 3-digit counter
    design = Design(
        id=None, code=code, product_line_id=seed_product_line,
        editor_user_id=seed_editor, editor_name='Ali',
        mockup_file_ids=['m1'], print_file_ids=['p1'],
    )
    design.save()
    assert design.reject(2001, 'Nazi')
    cursor = db_conn.cursor()
    try:
        cursor.execute('SELECT code, status FROM designs WHERE id = %s', (design.id,))
        rejected_code, status = cursor.fetchone()
        assert rejected_code.startswith(code + '_REJ_')
        assert len(rejected_code) > 20 and status == 'rejected'
    finally:
        cursor.close()


def test_mysql_code_width_migration_is_idempotent(db_conn):
    from migrations.migration_012_expand_design_code import Migration012

    cursor = db_conn.cursor()
    try:
        cursor.execute('ALTER TABLE designs MODIFY COLUMN code VARCHAR(20) NOT NULL')
        Migration012.up(cursor)
        Migration012.up(cursor)
        cursor.execute("SHOW COLUMNS FROM designs LIKE 'code'")
        assert 'varchar(40)' in cursor.fetchone()[1].lower()
    finally:
        cursor.close()
