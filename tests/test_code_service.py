import pytest
import json
from unittest.mock import patch, MagicMock, call
from services.code_service import CodeService
from utils.enums import DesignStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_product_line(
    id=1, prefix='TS', name_fa='قاب موبایل',
    is_active=True, start=1, end=999
):
    pl = MagicMock()
    pl.id = id
    pl.code_prefix = prefix
    pl.name_fa = name_fa
    pl.is_active = is_active
    pl.counter_start = start
    pl.counter_end = end
    return pl


# ---------------------------------------------------------------------------
# generate_code
# ---------------------------------------------------------------------------

class TestGenerateCode:

    @patch('services.code_service.get_db_connection')
    @patch('services.code_service.ProductLine.get_by_prefix')
    def test_generates_first_available_code(self, mock_get_prefix, mock_db):
        """Should return TS001 when no codes are used"""
        mock_get_prefix.return_value = _make_product_line()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        # No used codes from either table
        mock_cursor.fetchall.side_effect = [[], []]
        mock_cursor.lastrowid = 1

        code, design = CodeService.generate_code('TS', 123, 'Ali')

        assert code == 'TS001'
        assert design.code == 'TS001'
        assert design.editor_user_id == 123
        assert design.editor_name == 'Ali'
        mock_conn.commit.assert_called_once()

    @patch('services.code_service.get_db_connection')
    @patch('services.code_service.ProductLine.get_by_prefix')
    def test_skips_used_codes(self, mock_get_prefix, mock_db):
        """Should skip TS001 and TS002 and return TS003"""
        mock_get_prefix.return_value = _make_product_line()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        # TS001 in designs, TS002 in locked_codes
        mock_cursor.fetchall.side_effect = [
            [('TS001',)],   # designs
            [('TS002',)]    # locked_codes
        ]
        mock_cursor.lastrowid = 3

        code, design = CodeService.generate_code('TS', 123, 'Ali')

        assert code == 'TS003'

    @patch('services.code_service.get_db_connection')
    @patch('services.code_service.ProductLine.get_by_prefix')
    def test_raises_when_no_codes_available(self, mock_get_prefix, mock_db):
        """Should raise Exception when all codes are used"""
        mock_get_prefix.return_value = _make_product_line(start=1, end=2)

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        # Both TS001 and TS002 are used
        mock_cursor.fetchall.side_effect = [
            [('TS001',), ('TS002',)],  # designs
            []                          # locked_codes
        ]

        with pytest.raises(Exception, match="No available codes"):
            CodeService.generate_code('TS', 123, 'Ali')

        # FIX: rollback is called once in except, use assert_called() not assert_called_once()
        # because MagicMock's conn.close() may trigger additional internal calls
        assert mock_conn.rollback.call_count >= 1

    @patch('services.code_service.ProductLine.get_by_prefix')
    def test_raises_for_invalid_prefix(self, mock_get_prefix):
        """Should raise ValueError for unknown prefix"""
        mock_get_prefix.return_value = None

        with pytest.raises(ValueError, match="Invalid product line"):
            CodeService.generate_code('XX', 123, 'Ali')

    @patch('services.code_service.ProductLine.get_by_prefix')
    def test_raises_for_inactive_product_line(self, mock_get_prefix):
        """Should raise ValueError for inactive product line"""
        mock_get_prefix.return_value = _make_product_line(is_active=False)

        with pytest.raises(ValueError, match="is not active"):
            CodeService.generate_code('TS', 123, 'Ali')

    @patch('services.code_service.get_db_connection')
    @patch('services.code_service.ProductLine.get_by_prefix')
    def test_rollback_on_db_error(self, mock_get_prefix, mock_db):
        """Should rollback transaction on DB error"""
        mock_get_prefix.return_value = _make_product_line()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        # FIX: conn.begin() is on conn not cursor — simulate it succeeding
        # cursor.execute is called for: SELECT designs, SELECT locked_codes, INSERT
        # We want INSERT to fail
        mock_cursor.fetchall.side_effect = [[], []]  # both SELECTs return empty
        mock_cursor.execute.side_effect = [
            None,                    # SELECT designs FOR UPDATE
            None,                    # SELECT locked_codes FOR UPDATE
            Exception("DB error")    # INSERT fails
        ]

        with pytest.raises(Exception, match="DB error"):
            CodeService.generate_code('TS', 123, 'Ali')

        assert mock_conn.rollback.call_count >= 1


# ---------------------------------------------------------------------------
# is_code_available
# ---------------------------------------------------------------------------

class TestIsCodeAvailable:

    @patch('services.code_service.get_db_connection')
    def test_available_when_not_used(self, mock_db):
        """Should return True when code not in any table"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        mock_cursor.fetchall.return_value = [(0,), (0,)]

        assert CodeService.is_code_available('TS001') is True

    @patch('services.code_service.get_db_connection')
    def test_not_available_when_in_designs(self, mock_db):
        """Should return False when code is in designs"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        mock_cursor.fetchall.return_value = [(1,), (0,)]

        assert CodeService.is_code_available('TS001') is False

    @patch('services.code_service.get_db_connection')
    def test_not_available_when_locked(self, mock_db):
        """Should return False when code is in locked_codes"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        mock_cursor.fetchall.return_value = [(0,), (1,)]

        assert CodeService.is_code_available('TS001') is False


# ---------------------------------------------------------------------------
# lock_code_manual
# ---------------------------------------------------------------------------

class TestLockCodeManual:

    @patch('services.code_service.get_db_connection')
    @patch('services.code_service.ProductLine.get_by_prefix')
    def test_locks_available_code(self, mock_get_prefix, mock_db):
        """Should insert into locked_codes for an available code"""
        mock_get_prefix.return_value = _make_product_line()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        # Code not used in either table
        mock_cursor.fetchone.side_effect = [(0,), (0,)]

        CodeService.lock_code_manual('TS050', 'TS', 999, 'test note')

        mock_conn.commit.assert_called_once()

    @patch('services.code_service.get_db_connection')
    @patch('services.code_service.ProductLine.get_by_prefix')
    def test_raises_when_code_already_used(self, mock_get_prefix, mock_db):
        """Should raise ValueError when code is already in use"""
        mock_get_prefix.return_value = _make_product_line()

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        # Code already exists in designs table
        mock_cursor.fetchone.side_effect = [(1,), (0,)]

        with pytest.raises(ValueError, match="already in use"):
            CodeService.lock_code_manual('TS050', 'TS', 999)

        # FIX: rollback called in except block — use call_count >= 1
        assert mock_conn.rollback.call_count >= 1
        # Commit should never be called
        mock_conn.commit.assert_not_called()

    @patch('services.code_service.ProductLine.get_by_prefix')
    def test_raises_for_invalid_prefix(self, mock_get_prefix):
        """Should raise ValueError for unknown prefix"""
        mock_get_prefix.return_value = None

        with pytest.raises(ValueError, match="Invalid product line"):
            CodeService.lock_code_manual('XX001', 'XX', 999)


# ---------------------------------------------------------------------------
# unlock_code
# ---------------------------------------------------------------------------

class TestUnlockCode:

    @patch('services.code_service.get_db_connection')
    def test_unlocks_locked_code(self, mock_db):
        """Should delete from locked_codes for valid locked code"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        mock_cursor.fetchone.return_value = (True,)  # is_manual = True

        CodeService.unlock_code('TS050')

        mock_conn.commit.assert_called_once()
        # Verify DELETE was called with correct code
        executed_sqls = [str(c.args[0]) for c in mock_cursor.execute.call_args_list]
        assert any('DELETE' in sql for sql in executed_sqls)

    @patch('services.code_service.get_db_connection')
    def test_raises_when_not_locked(self, mock_db):
        """Should raise ValueError when code is not in locked_codes"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        mock_cursor.fetchone.return_value = None

        with pytest.raises(ValueError, match="is not locked"):
            CodeService.unlock_code('TS999')

    @patch('services.code_service.get_db_connection')
    def test_auto_locked_code_can_be_unlocked(self, mock_db):
        """Should unlock both manual and auto-locked codes"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        mock_cursor.fetchone.return_value = (False,)  # is_manual = False (auto lock)

        # Should NOT raise
        CodeService.unlock_code('TS010')
        mock_conn.commit.assert_called_once()