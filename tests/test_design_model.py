import pytest
import json
from unittest.mock import patch, MagicMock
from models.design import Design
from utils.enums import DesignStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_raw_row(**overrides):
    """Return a raw DB dict as pymysql would return it."""
    base = {
        'id': 1,
        'code': 'TS001',
        'product_line_id': 1,
        'status': 'pending',
        'editor_user_id': 100,
        'editor_name': 'Ali',
        'reviewer_user_id': None,
        'reviewer_name': None,
        'mockup_file_ids': json.dumps(['file1', 'file2']),
        'print_file_ids': json.dumps(['pfile1']),
        'mockup_message_ids_reviewer': None,
        'created_at': None,
        'reviewed_at': None,
        'final_name': None,
        'metadata': None,
        'product_name': None,
        'product_icon': None,
    }
    base.update(overrides)
    return base


def _make_design(**overrides) -> Design:
    row = _make_raw_row(**overrides)
    return Design(**Design._parse_row(row))


# ---------------------------------------------------------------------------
# _parse_row
# ---------------------------------------------------------------------------

class TestParseRow:

    def test_parses_valid_json_fields(self):
        row = _make_raw_row()
        parsed = Design._parse_row(row)
        assert parsed['mockup_file_ids'] == ['file1', 'file2']
        assert parsed['print_file_ids'] == ['pfile1']
        assert parsed['mockup_message_ids_reviewer'] == {}

    def test_handles_empty_mockup_file_ids(self):
        row = _make_raw_row(mockup_file_ids=None)
        parsed = Design._parse_row(row)
        assert parsed['mockup_file_ids'] == []

    def test_handles_malformed_mockup_json(self):
        row = _make_raw_row(mockup_file_ids='{invalid json}')
        parsed = Design._parse_row(row)
        assert parsed['mockup_file_ids'] == []

    def test_handles_malformed_print_json(self):
        row = _make_raw_row(print_file_ids='{bad}')
        parsed = Design._parse_row(row)
        assert parsed['print_file_ids'] == []

    def test_converts_legacy_list_reviewer_msgs_to_dict(self):
        row = _make_raw_row(
            mockup_message_ids_reviewer=json.dumps([1, 2, 3])
        )
        parsed = Design._parse_row(row)
        assert parsed['mockup_message_ids_reviewer'] == {'legacy': [1, 2, 3]}

    def test_preserves_dict_reviewer_msgs(self):
        msgs = {'123': [10, 11], '456': [20]}
        row = _make_raw_row(
            mockup_message_ids_reviewer=json.dumps(msgs)
        )
        parsed = Design._parse_row(row)
        assert parsed['mockup_message_ids_reviewer'] == msgs

    def test_handles_malformed_reviewer_json(self):
        row = _make_raw_row(mockup_message_ids_reviewer='{bad}')
        parsed = Design._parse_row(row)
        assert parsed['mockup_message_ids_reviewer'] == {}


# ---------------------------------------------------------------------------
# Design.__init__ and status handling
# ---------------------------------------------------------------------------

class TestDesignInit:

    def test_status_converted_to_enum(self):
        d = _make_design(status='pending')
        assert d.status == DesignStatus.PENDING
        assert isinstance(d.status, DesignStatus)

    def test_approved_status(self):
        d = _make_design(status='approved')
        assert d.status == DesignStatus.APPROVED

    def test_rejected_status(self):
        d = _make_design(status='rejected')
        assert d.status == DesignStatus.REJECTED

    def test_deleted_status(self):
        d = _make_design(status='deleted')
        assert d.status == DesignStatus.DELETED

    def test_defaults_empty_lists(self):
        d = _make_design(
            mockup_file_ids=json.dumps([]),
            print_file_ids=json.dumps([])
        )
        assert d.mockup_file_ids == []
        assert d.print_file_ids == []

    def test_unknown_kwargs_dont_raise(self):
        """Unknown DB columns should log warning but not crash"""
        row = _make_raw_row()
        row['future_column'] = 'some_value'
        # Should not raise
        d = Design(**Design._parse_row(row))
        assert d.code == 'TS001'


# ---------------------------------------------------------------------------
# reviewer message tracking
# ---------------------------------------------------------------------------

class TestReviewerMessages:

    def test_set_and_get_reviewer_messages(self):
        d = _make_design()
        d.set_reviewer_messages(999, [10, 11, 12])
        assert d.get_reviewer_messages(999) == [10, 11, 12]

    def test_get_missing_reviewer_returns_empty(self):
        d = _make_design()
        assert d.get_reviewer_messages(999) == []

    def test_all_reviewer_message_pairs_excludes_legacy(self):
        d = _make_design()
        d.mockup_message_ids_reviewer = {
            'legacy': [1, 2],
            '100': [10, 11],
            '200': [20]
        }
        pairs = list(d.all_reviewer_message_pairs())
        reviewer_ids = [uid for uid, _ in pairs]
        assert 100 in reviewer_ids
        assert 200 in reviewer_ids
        # legacy key should be excluded
        assert 'legacy' not in [str(uid) for uid in reviewer_ids]

    def test_all_reviewer_message_pairs_skips_invalid_keys(self):
        d = _make_design()
        d.mockup_message_ids_reviewer = {
            'not_a_number': [1, 2],
            '100': [10]
        }
        pairs = list(d.all_reviewer_message_pairs())
        assert len(pairs) == 1
        assert pairs[0][0] == 100


# ---------------------------------------------------------------------------
# approve / reject
# ---------------------------------------------------------------------------

class TestApprove:

    @patch('models.design.get_db_connection')
    def test_approve_updates_status(self, mock_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn
        mock_cursor.rowcount = 1

        d = _make_design()
        with patch.object(d, 'lock_code'):
            result = d.approve(reviewer_user_id=200, reviewer_name='Nazi')

        assert result is True
        assert d.status == DesignStatus.APPROVED
        assert d.reviewer_user_id == 200
        assert d.reviewer_name == 'Nazi'
        mock_conn.commit.assert_called_once()

    @patch('models.design.get_db_connection')
    def test_approve_returns_false_when_already_processed(self, mock_db):
        """Simulates race condition where another reviewer acted first"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn
        mock_cursor.rowcount = 0  # No rows affected = already processed

        d = _make_design()
        result = d.approve(reviewer_user_id=200, reviewer_name='Nazi')

        assert result is False
        assert d.status == DesignStatus.PENDING  # Status should not change


class TestReject:

    @patch('models.design.get_db_connection')
    def test_reject_updates_status(self, mock_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn
        mock_cursor.rowcount = 1

        d = _make_design()
        result = d.reject(reviewer_user_id=200, reviewer_name='Nazi')

        assert result is True
        assert d.status == DesignStatus.REJECTED
        assert d.reviewer_user_id == 200
        mock_conn.commit.assert_called_once()

    @patch('models.design.get_db_connection')
    def test_reject_returns_false_when_already_processed(self, mock_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn
        mock_cursor.rowcount = 0

        d = _make_design()
        result = d.reject(reviewer_user_id=200, reviewer_name='Nazi')

        assert result is False


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

class TestSave:

    @patch('models.design.get_db_connection')
    def test_save_inserts_new_design(self, mock_db):
        """Should INSERT when id is None"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn
        mock_cursor.lastrowid = 42

        d = Design(
            id=None, code='TS005', product_line_id=1,
            editor_user_id=100, editor_name='Ali'
        )
        d.save()

        assert d.id == 42
        mock_conn.commit.assert_called_once()
        call_args = mock_cursor.execute.call_args[0]
        assert 'INSERT' in call_args[0]

    @patch('models.design.get_db_connection')
    def test_save_updates_existing_design(self, mock_db):
        """Should UPDATE when id is set"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        d = _make_design()
        d.save()

        call_args = mock_cursor.execute.call_args[0]
        assert 'UPDATE' in call_args[0]
        mock_conn.commit.assert_called_once()

    @patch('models.design.get_db_connection')
    def test_save_rollback_on_error(self, mock_db):
        """Should rollback and re-raise on DB error"""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn
        mock_cursor.execute.side_effect = Exception("DB error")

        d = _make_design()
        with pytest.raises(Exception, match="DB error"):
            d.save()

        mock_conn.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

class TestDelete:

    @patch('models.design.get_db_connection')
    def test_delete_removes_design(self, mock_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn

        d = _make_design()
        d.delete()

        mock_conn.commit.assert_called_once()
        call_args = mock_cursor.execute.call_args[0]
        assert 'DELETE' in call_args[0]

    @patch('models.design.get_db_connection')
    def test_delete_rollback_on_error(self, mock_db):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_db.return_value = mock_conn
        mock_cursor.execute.side_effect = Exception("DB error")

        d = _make_design()
        with pytest.raises(Exception):
            d.delete()

        mock_conn.rollback.assert_called_once()