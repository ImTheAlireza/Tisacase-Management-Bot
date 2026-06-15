import pytest
import pymysql
from unittest.mock import patch
from tests.conftest import get_test_connection


@pytest.fixture(autouse=True)
def use_test_db(test_db):
    with patch('models.product_line.get_db_connection', side_effect=get_test_connection):
        yield


def _get_pl_row(prefix: str):
    conn = get_test_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute(
            "SELECT * FROM product_lines WHERE code_prefix = %s", (prefix,)
        )
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def _insert_pl(db_conn, prefix='TS', is_active=True):
    cursor = db_conn.cursor()
    cursor.execute("""
        INSERT INTO product_lines
        (code_prefix, name_en, name_fa, icon, counter_start,
         counter_end, is_active, display_order)
        VALUES (%s, 'case', 'قاب موبایل', '📱', 1, 999, %s, 1)
    """, (prefix, is_active))
    db_conn.commit()
    pl_id = cursor.lastrowid
    cursor.close()
    return pl_id


class TestGetByPrefix:

    def test_returns_active_line(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS', is_active=True)
        pl = ProductLine.get_by_prefix('TS')
        assert pl is not None
        assert pl.code_prefix == 'TS'

    def test_returns_none_for_inactive(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS', is_active=False)
        pl = ProductLine.get_by_prefix('TS')
        assert pl is None

    def test_returns_inactive_when_active_only_false(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS', is_active=False)
        pl = ProductLine.get_by_prefix('TS', active_only=False)
        assert pl is not None
        assert not pl.is_active

    def test_returns_none_for_unknown_prefix(self, db_conn):
        from models.product_line import ProductLine
        assert ProductLine.get_by_prefix('XX') is None


class TestGetAll:

    def test_get_all_active_excludes_inactive(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS', is_active=True)
        _insert_pl(db_conn, prefix='STI', is_active=False)
        active = ProductLine.get_all_active()
        prefixes = [pl.code_prefix for pl in active]
        assert 'TS' in prefixes
        assert 'STI' not in prefixes

    def test_get_all_includes_inactive(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS', is_active=True)
        _insert_pl(db_conn, prefix='STI', is_active=False)
        all_lines = ProductLine.get_all()
        prefixes = [pl.code_prefix for pl in all_lines]
        assert 'TS' in prefixes
        assert 'STI' in prefixes

    def test_returns_empty_when_no_lines(self, db_conn):
        from models.product_line import ProductLine
        assert ProductLine.get_all_active() == []


class TestCreate:

    def test_creates_product_line(self, db_conn):
        from models.product_line import ProductLine
        pl = ProductLine.create(
            code_prefix='MG',
            name_en='mug',
            name_fa='ماگ',
            icon='☕'
        )
        assert pl is not None
        assert pl.code_prefix == 'MG'
        assert pl.name_fa == 'ماگ'
        assert pl.is_active
        row = _get_pl_row('MG')
        assert row is not None
        assert row['icon'] == '☕'

    def test_duplicate_prefix_raises(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS')
        with pytest.raises(Exception):
            ProductLine.create(
                code_prefix='TS',
                name_en='case2',
                name_fa='قاب دوم',
                icon='📱'
            )


class TestActivateDeactivate:

    def test_deactivate(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS', is_active=True)
        pl = ProductLine.get_by_prefix('TS')
        pl.deactivate()
        row = _get_pl_row('TS')
        assert not row['is_active']

    def test_activate(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS', is_active=False)
        pl = ProductLine.get_by_prefix('TS', active_only=False)
        pl.activate()
        row = _get_pl_row('TS')
        assert row['is_active']

    def test_deactivated_line_not_returned_by_get_all_active(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS', is_active=True)
        pl = ProductLine.get_by_prefix('TS')
        pl.deactivate()
        active = ProductLine.get_all_active()
        assert all(p.code_prefix != 'TS' for p in active)


class TestSetGroup:

    def test_set_products_group(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS')
        pl = ProductLine.get_by_prefix('TS')
        pl.set_group('products', -1001234567890)
        row = _get_pl_row('TS')
        assert row['group_products'] == -1001234567890

    def test_set_print_group(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS')
        pl = ProductLine.get_by_prefix('TS')
        pl.set_group('print', -1009876543210)
        row = _get_pl_row('TS')
        assert row['group_print'] == -1009876543210

    def test_is_fully_configured_false_when_missing_groups(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS')
        pl = ProductLine.get_by_prefix('TS')
        assert not pl.is_fully_configured()

    def test_is_fully_configured_true_when_both_set(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS')
        pl = ProductLine.get_by_prefix('TS')
        pl.set_group('products', -1001111111111)
        pl.set_group('print', -1002222222222)
        pl_reloaded = ProductLine.get_by_prefix('TS')
        assert pl_reloaded.is_fully_configured()

    def test_invalid_group_type_raises(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS')
        pl = ProductLine.get_by_prefix('TS')
        with pytest.raises(ValueError, match="group_type must be"):
            pl.set_group('invalid_type', -1001234567890)

    def test_missing_groups_returns_correct_list(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS')
        pl = ProductLine.get_by_prefix('TS')
        missing = pl.missing_groups()
        assert len(missing) == 2
        assert any('group_products' in m for m in missing)
        assert any('group_print' in m for m in missing)

    def test_missing_groups_empty_when_configured(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS')
        pl = ProductLine.get_by_prefix('TS')
        pl.set_group('products', -1001111111111)
        pl.set_group('print', -1002222222222)
        pl_reloaded = ProductLine.get_by_prefix('TS')
        assert pl_reloaded.missing_groups() == []


class TestGetStats:

    def test_stats_all_zeros_when_empty(self, db_conn):
        from models.product_line import ProductLine
        _insert_pl(db_conn, prefix='TS')
        pl = ProductLine.get_by_prefix('TS')
        stats = pl.get_stats()
        assert stats['pending'] == 0
        assert stats['approved'] == 0
        assert stats['rejected'] == 0
        assert stats['locked'] == 0

    def test_stats_counts_correctly(self, db_conn):
        from models.product_line import ProductLine
        pl_id = _insert_pl(db_conn, prefix='TS')
        cursor = db_conn.cursor()
        for code, status in [
            ('TS001', 'pending'),
            ('TS002', 'pending'),
            ('TS003', 'approved'),
            ('TS004', 'rejected'),
        ]:
            cursor.execute("""
                INSERT INTO designs
                (code, product_line_id, status, editor_user_id,
                 editor_name, mockup_file_ids, print_file_ids)
                VALUES (%s, %s, %s, 1001, 'Ali', '[]', '[]')
            """, (code, pl_id, status))
        cursor.execute("""
            INSERT INTO designs_locked_codes
            (code, product_line_id, is_manual)
            VALUES ('TS050', %s, TRUE)
        """, (pl_id,))
        db_conn.commit()
        cursor.close()
        pl = ProductLine.get_by_prefix('TS')
        stats = pl.get_stats()
        assert stats['pending'] == 2
        assert stats['approved'] == 1
        assert stats['rejected'] == 1
        assert stats['locked'] == 1
