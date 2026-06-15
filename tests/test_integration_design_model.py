import pytest
import pymysql
import json
import time
from unittest.mock import patch
from tests.conftest import get_test_connection
from utils.enums import DesignStatus


@pytest.fixture(autouse=True)
def use_test_db(test_db):
    with patch('models.design.get_db_connection', side_effect=get_test_connection), \
         patch('models.product_line.get_db_connection', side_effect=get_test_connection), \
         patch('models.user.get_db_connection', side_effect=get_test_connection), \
         patch('services.code_service.get_db_connection', side_effect=get_test_connection):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_product_line(db_conn, prefix='TS', start=1, end=999) -> int:
    cursor = db_conn.cursor()
    cursor.execute("""
        INSERT INTO product_lines
        (code_prefix, name_en, name_fa, icon, counter_start,
         counter_end, is_active, display_order)
        VALUES (%s, 'case', 'قاب موبایل', '📱', %s, %s, TRUE, 1)
    """, (prefix, start, end))
    db_conn.commit()
    pl_id = cursor.lastrowid
    cursor.close()
    return pl_id


def _insert_design(db_conn, code='TS001', pl_id=1, status='pending',
                   editor_id=1001, mockups=None, prints=None) -> int:
    cursor = db_conn.cursor()
    cursor.execute("""
        INSERT INTO designs
        (code, product_line_id, status, editor_user_id, editor_name,
         mockup_file_ids, print_file_ids)
        VALUES (%s, %s, %s, %s, 'Ali', %s, %s)
    """, (
        code, pl_id, status, editor_id,
        json.dumps(mockups or []),
        json.dumps(prints or [])
    ))
    db_conn.commit()
    design_id = cursor.lastrowid
    cursor.close()
    return design_id


def _get_design_row(code: str) -> dict | None:
    conn = get_test_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute("SELECT * FROM designs WHERE code = %s", (code,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def _get_locked_row(code: str) -> dict | None:
    conn = get_test_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute(
            "SELECT * FROM designs_locked_codes WHERE code = %s", (code,)
        )
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------------
# get_by_code
# ---------------------------------------------------------------------------
class TestGetByCode:

    def test_returns_none_for_missing_code(self, db_conn):
        from models.design import Design
        assert Design.get_by_code('TS999') is None

    def test_returns_design_for_existing_code(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, code='TS001', pl_id=pl_id,
                       mockups=['file1'], prints=['pfile1'])

        design = Design.get_by_code('TS001')

        assert design is not None
        assert design.code == 'TS001'
        assert design.status == DesignStatus.PENDING
        assert design.mockup_file_ids == ['file1']
        assert design.print_file_ids == ['pfile1']

    def test_parses_reviewer_messages_from_db(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        design_id = _insert_design(db_conn, pl_id=pl_id)

        # Manually set reviewer messages
        msgs = {'2001': [10, 11, 12]}
        cursor = db_conn.cursor()
        cursor.execute(
            "UPDATE designs SET mockup_message_ids_reviewer = %s WHERE id = %s",
            (json.dumps(msgs), design_id)
        )
        db_conn.commit()
        cursor.close()

        design = Design.get_by_code('TS001')
        assert design.get_reviewer_messages(2001) == [10, 11, 12]

    def test_parse_row_handles_none_values(self):
        """Test _parse_row with NULL JSON fields"""
        from models.design import Design
        
        row = {
            'id': 1, 'code': 'TS001', 'product_line_id': 1,
            'status': 'pending', 'editor_user_id': 1001,
            'editor_name': 'Ali', 'reviewer_user_id': None,
            'reviewer_name': None, 'created_at': None,
            'reviewed_at': None, 'final_name': None,
            'metadata': None, 'product_name': None,
            'product_icon': None,
            'mockup_file_ids': None,
            'print_file_ids': None,
            'mockup_message_ids_reviewer': None,
        }
        
        parsed = Design._parse_row(row)
        assert parsed['mockup_file_ids'] == []
        assert parsed['print_file_ids'] == []
        assert parsed['mockup_message_ids_reviewer'] == {}

    def test_parse_row_handles_empty_string(self):
        """Test _parse_row with empty string (defensive coding)"""
        from models.design import Design
        
        row = {
            'id': 1, 'code': 'TS001', 'product_line_id': 1,
            'status': 'pending', 'editor_user_id': 1001,
            'editor_name': 'Ali', 'reviewer_user_id': None,
            'reviewer_name': None, 'created_at': None,
            'reviewed_at': None, 'final_name': None,
            'metadata': None, 'product_name': None,
            'product_icon': None,
            'mockup_file_ids': '',
            'print_file_ids': '',
            'mockup_message_ids_reviewer': '',
        }
        
        parsed = Design._parse_row(row)
        assert parsed['mockup_file_ids'] == []
        assert parsed['print_file_ids'] == []
        assert parsed['mockup_message_ids_reviewer'] == {}

    def test_parse_row_handles_valid_json_arrays(self):
        """Test _parse_row with properly formatted JSON strings"""
        from models.design import Design
        
        row = {
            'id': 1, 'code': 'TS001', 'product_line_id': 1,
            'status': 'pending', 'editor_user_id': 1001,
            'editor_name': 'Ali', 'reviewer_user_id': None,
            'reviewer_name': None, 'created_at': None,
            'reviewed_at': None, 'final_name': None,
            'metadata': None, 'product_name': None,
            'product_icon': None,
            'mockup_file_ids': '["file1", "file2"]',
            'print_file_ids': '["pfile1"]',
            'mockup_message_ids_reviewer': '{"2001": [10, 11]}',
        }
        
        parsed = Design._parse_row(row)
        assert parsed['mockup_file_ids'] == ['file1', 'file2']
        assert parsed['print_file_ids'] == ['pfile1']
        assert parsed['mockup_message_ids_reviewer'] == {'2001': [10, 11]}

# ---------------------------------------------------------------------------
# get_all_pending
# ---------------------------------------------------------------------------

class TestGetAllPending:

    def test_returns_only_pending(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, code='TS001', pl_id=pl_id, status='pending')
        _insert_design(db_conn, code='TS002', pl_id=pl_id, status='approved')
        _insert_design(db_conn, code='TS003', pl_id=pl_id, status='rejected')
        _insert_design(db_conn, code='TS004', pl_id=pl_id, status='pending')

        pending = Design.get_all_pending()
        codes = [d.code for d in pending]

        assert 'TS001' in codes
        assert 'TS004' in codes
        assert 'TS002' not in codes
        assert 'TS003' not in codes

    def test_returns_empty_when_no_pending(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, code='TS001', pl_id=pl_id, status='approved')

        assert Design.get_all_pending() == []

    def test_includes_product_info(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn, prefix='TS')
        _insert_design(db_conn, code='TS001', pl_id=pl_id, status='pending')

        pending = Design.get_all_pending()

        assert len(pending) == 1
        assert pending[0].product_name == 'قاب موبایل'
        assert pending[0].product_icon == '📱'

    def test_ordered_by_created_at_asc(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)

        cursor = db_conn.cursor()
        cursor.execute("""
            INSERT INTO designs
            (code, product_line_id, status, editor_user_id,
             editor_name, mockup_file_ids, print_file_ids, created_at)
            VALUES
            ('TS003', %s, 'pending', 1001, 'Ali', '[]', '[]', NOW() - INTERVAL 1 HOUR),
            ('TS001', %s, 'pending', 1001, 'Ali', '[]', '[]', NOW() - INTERVAL 3 HOUR),
            ('TS002', %s, 'pending', 1001, 'Ali', '[]', '[]', NOW() - INTERVAL 2 HOUR)
        """, (pl_id, pl_id, pl_id))
        db_conn.commit()
        cursor.close()

        pending = Design.get_all_pending()
        codes = [d.code for d in pending]

        assert codes == ['TS001', 'TS002', 'TS003']


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

class TestSaveDesign:

    def test_insert_new_design(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)

        design = Design(
            id=None, code='TS001', product_line_id=pl_id,
            editor_user_id=1001, editor_name='Ali',
            mockup_file_ids=['m1', 'm2'],
            print_file_ids=['p1']
        )
        design.save()

        assert design.id is not None
        row = _get_design_row('TS001')
        assert row is not None
        assert json.loads(row['mockup_file_ids']) == ['m1', 'm2']
        assert json.loads(row['print_file_ids']) == ['p1']

    def test_update_existing_design(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id)

        design = Design.get_by_code('TS001')
        design.mockup_file_ids = ['new_file']
        design.print_file_ids = ['new_print']
        design.save()

        row = _get_design_row('TS001')
        assert json.loads(row['mockup_file_ids']) == ['new_file']
        assert json.loads(row['print_file_ids']) == ['new_print']

    def test_save_reviewer_messages(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id)

        design = Design.get_by_code('TS001')
        design.set_reviewer_messages(2001, [10, 11, 12])
        design.save()

        reloaded = Design.get_by_code('TS001')
        assert reloaded.get_reviewer_messages(2001) == [10, 11, 12]

    def test_duplicate_code_raises(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id, code='TS001')

        design2 = Design(
            id=None, code='TS001', product_line_id=pl_id,
            editor_user_id=1002, editor_name='Bob'
        )
        with pytest.raises(Exception):
            design2.save()


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------

class TestApproveDesign:

    def test_approve_updates_db_status(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id,
                       mockups=['m1'], prints=['p1'])

        design = Design.get_by_code('TS001')
        result = design.approve(2001, 'Nazi')

        assert result
        row = _get_design_row('TS001')
        assert row['status'] == 'approved'
        assert row['reviewer_user_id'] == 2001
        assert row['reviewer_name'] == 'Nazi'
        assert row['reviewed_at'] is not None

    def test_approve_creates_locked_code(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id,
                       mockups=['m1'], prints=['p1'])

        design = Design.get_by_code('TS001')
        design.approve(2001, 'Nazi')

        locked = _get_locked_row('TS001')
        assert locked is not None
        assert locked['is_manual'] == 0

    def test_approve_already_approved_returns_false(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id,
                       mockups=['m1'], prints=['p1'])

        design1 = Design.get_by_code('TS001')
        design2 = Design.get_by_code('TS001')

        result1 = design1.approve(2001, 'Nazi')
        result2 = design2.approve(3001, 'Other')

        assert result1
        assert not result2

        # DB should only have first reviewer
        row = _get_design_row('TS001')
        assert row['reviewer_name'] == 'Nazi'

    def test_approve_rejected_design_returns_false(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id, status='rejected',
                       code='TS001_REJ_123')

        design = Design.get_by_code('TS001_REJ_123')
        result = design.approve(2001, 'Nazi')

        assert not result


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------

class TestRejectDesign:

    def test_reject_renames_code(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id,
                       mockups=['m1'], prints=['p1'])

        design = Design.get_by_code('TS001')
        result = design.reject(2001, 'Nazi')

        assert result

        # Original code should be gone
        assert _get_design_row('TS001') is None

        # _REJ_ code should exist
        conn = get_test_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT * FROM designs WHERE code LIKE %s", ('TS001_REJ_%',)
        )
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        assert row is not None
        assert row['status'] == 'rejected'
        assert row['reviewer_name'] == 'Nazi'

    def test_reject_does_not_lock_code(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id,
                       mockups=['m1'], prints=['p1'])

        design = Design.get_by_code('TS001')
        design.reject(2001, 'Nazi')

        # Code should NOT be in locked_codes
        assert _get_locked_row('TS001') is None

    def test_reject_frees_original_code_slot(self, db_conn):
        from models.design import Design
        from services.code_service import CodeService
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id,
                       mockups=['m1'], prints=['p1'])

        design = Design.get_by_code('TS001')
        design.reject(2001, 'Nazi')

        # TS001 should be available for reuse
        assert CodeService.is_code_available('TS001')

    def test_reject_already_rejected_returns_false(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id,
                       mockups=['m1'], prints=['p1'])

        design1 = Design.get_by_code('TS001')
        design2 = Design.get_by_code('TS001')

        result1 = design1.reject(2001, 'Nazi')
        result2 = design2.reject(3001, 'Other')

        assert result1
        assert not result2


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

class TestDeleteDesign:

    def test_delete_removes_from_db(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id)

        design = Design.get_by_code('TS001')
        design.delete()

        assert _get_design_row('TS001') is None

    def test_delete_nonexistent_raises(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id)

        design = Design.get_by_code('TS001')
        design.delete()

        # Deleting again should not raise (rowcount = 0 is fine)
        design.delete()


# ---------------------------------------------------------------------------
# lock_code
# ---------------------------------------------------------------------------

class TestLockCode:

    def test_lock_code_inserts_record(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id)

        design = Design.get_by_code('TS001')
        design.lock_code()

        locked = _get_locked_row('TS001')
        assert locked is not None
        assert locked['is_manual'] == 0
        assert locked['product_line_id'] == pl_id

    def test_lock_code_upserts_on_duplicate(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id)

        design = Design.get_by_code('TS001')

        # Call twice — should not raise
        design.lock_code()
        design.lock_code()

        locked = _get_locked_row('TS001')
        assert locked is not None


# ---------------------------------------------------------------------------
# reviewer message tracking (round-trip)
# ---------------------------------------------------------------------------

class TestReviewerMessageRoundTrip:

    def test_set_save_reload(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        _insert_design(db_conn, pl_id=pl_id)

        design = Design.get_by_code('TS001')
        design.set_reviewer_messages(2001, [10, 11])
        design.set_reviewer_messages(3001, [20, 21])
        design.save()

        reloaded = Design.get_by_code('TS001')
        assert reloaded.get_reviewer_messages(2001) == [10, 11]
        assert reloaded.get_reviewer_messages(3001) == [20, 21]
        assert reloaded.get_reviewer_messages(9999) == []

    def test_legacy_list_format_converted(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        design_id = _insert_design(db_conn, pl_id=pl_id)

        # Inject legacy list format directly
        cursor = db_conn.cursor()
        cursor.execute(
            "UPDATE designs SET mockup_message_ids_reviewer = %s WHERE id = %s",
            (json.dumps([100, 101, 102]), design_id)
        )
        db_conn.commit()
        cursor.close()

        design = Design.get_by_code('TS001')
        # Legacy list should be under 'legacy' key, not crash
        assert design.mockup_message_ids_reviewer == {'legacy': [100, 101, 102]}

    def test_all_reviewer_pairs_excludes_legacy(self, db_conn):
        from models.design import Design
        pl_id = _insert_product_line(db_conn)
        design_id = _insert_design(db_conn, pl_id=pl_id)

        msgs = {'legacy': [1, 2], '2001': [10, 11], '3001': [20]}
        cursor = db_conn.cursor()
        cursor.execute(
            "UPDATE designs SET mockup_message_ids_reviewer = %s WHERE id = %s",
            (json.dumps(msgs), design_id)
        )
        db_conn.commit()
        cursor.close()

        design = Design.get_by_code('TS001')
        pairs = list(design.all_reviewer_message_pairs())
        reviewer_ids = [uid for uid, _ in pairs]

        assert 2001 in reviewer_ids
        assert 3001 in reviewer_ids
        assert 'legacy' not in [str(uid) for uid in reviewer_ids]