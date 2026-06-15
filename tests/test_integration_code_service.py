import pytest
import pymysql
from unittest.mock import patch
from tests.conftest import get_test_connection
from utils.enums import DesignStatus


# ---------------------------------------------------------------------------
# Patch get_db_connection to use test DB for all integration tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def use_test_db(test_db):
    """Redirect all DB calls to test database."""
    with patch('services.code_service.get_db_connection', side_effect=get_test_connection), \
         patch('models.product_line.get_db_connection', side_effect=get_test_connection), \
         patch('models.design.get_db_connection', side_effect=get_test_connection):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_design(code: str) -> dict | None:
    conn = get_test_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute("SELECT * FROM designs WHERE code = %s", (code,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


def _get_locked(code: str) -> dict | None:
    conn = get_test_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)
    try:
        cursor.execute("SELECT * FROM designs_locked_codes WHERE code = %s", (code,))
        return cursor.fetchone()
    finally:
        cursor.close()
        conn.close()


# ---------------------------------------------------------------------------
# Integration: generate_code
# ---------------------------------------------------------------------------

class TestIntegrationGenerateCode:

    def test_generates_first_code_in_empty_db(self, seed_product_line, seed_editor):
        """Real DB: first code should be TS001"""
        from services.code_service import CodeService

        code, design = CodeService.generate_code('TS', seed_editor, 'Ali')

        assert code == 'TS001'
        assert design.code == 'TS001'
        assert design.status == DesignStatus.PENDING

        # Verify it's in the DB
        row = _get_design('TS001')
        assert row is not None
        assert row['editor_user_id'] == seed_editor
        assert row['status'] == 'pending'

    def test_generates_sequential_codes(self, seed_product_line, seed_editor):
        """Real DB: each call should get the next available code"""
        from services.code_service import CodeService

        code1, _ = CodeService.generate_code('TS', seed_editor, 'Ali')
        code2, _ = CodeService.generate_code('TS', seed_editor, 'Ali')
        code3, _ = CodeService.generate_code('TS', seed_editor, 'Ali')

        assert code1 == 'TS001'
        assert code2 == 'TS002'
        assert code3 == 'TS003'

    def test_skips_locked_codes(self, seed_product_line, seed_editor):
        """Real DB: should skip manually locked codes"""
        from services.code_service import CodeService

        # Manually lock TS001
        conn = get_test_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO designs_locked_codes (code, product_line_id, is_manual)
            VALUES ('TS001', %s, TRUE)
        """, (seed_product_line,))
        conn.commit()
        cursor.close()
        conn.close()

        code, _ = CodeService.generate_code('TS', seed_editor, 'Ali')
        assert code == 'TS002'

    def test_skips_rejected_codes(self, seed_product_line, seed_editor):
        """Real DB: rejected code slot (renamed) should not block next code"""
        from services.code_service import CodeService

        # Simulate a rejected design (code renamed with _REJ_ suffix)
        conn = get_test_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO designs
            (code, product_line_id, status, editor_user_id,
             editor_name, mockup_file_ids, print_file_ids)
            VALUES ('TS001_REJ_9999', %s, 'rejected', %s, 'Ali', '[]', '[]')
        """, (seed_product_line, seed_editor))
        conn.commit()
        cursor.close()
        conn.close()

        # TS001 should be available (the _REJ_ rename freed it)
        code, _ = CodeService.generate_code('TS', seed_editor, 'Ali')
        assert code == 'TS001'

    def test_raises_when_all_codes_used(self, seed_product_line, seed_editor):
        """Real DB: raises when counter range is exhausted"""
        from services.code_service import CodeService

        # Set counter_end to 2 to exhaust quickly
        conn = get_test_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE product_lines SET counter_end = 2 WHERE id = %s",
            (seed_product_line,)
        )
        conn.commit()
        cursor.close()
        conn.close()

        CodeService.generate_code('TS', seed_editor, 'Ali')  # TS001
        CodeService.generate_code('TS', seed_editor, 'Ali')  # TS002

        with pytest.raises(Exception, match="No available codes"):
            CodeService.generate_code('TS', seed_editor, 'Ali')  # Should fail


# ---------------------------------------------------------------------------
# Integration: lock_code_manual / unlock_code
# ---------------------------------------------------------------------------

class TestIntegrationLockUnlock:

    def test_lock_and_unlock_cycle(self, seed_product_line, seed_editor):
        """Real DB: lock then unlock a code"""
        from services.code_service import CodeService

        CodeService.lock_code_manual('TS050', 'TS', seed_editor, 'Reserved for client')

        locked = _get_locked('TS050')
        assert locked is not None
        assert locked['is_manual'] == 1
        assert locked['notes'] == 'Reserved for client'

        CodeService.unlock_code('TS050')

        assert _get_locked('TS050') is None

    def test_locked_code_skipped_in_generation(self, seed_product_line, seed_editor):
        """Real DB: manually locked code should be skipped during generation"""
        from services.code_service import CodeService

        CodeService.lock_code_manual('TS001', 'TS', seed_editor)
        CodeService.lock_code_manual('TS002', 'TS', seed_editor)

        code, _ = CodeService.generate_code('TS', seed_editor, 'Ali')
        assert code == 'TS003'

    def test_cannot_lock_already_used_code(self, seed_product_line, seed_editor):
        """Real DB: cannot lock a code that's already in designs"""
        from services.code_service import CodeService

        CodeService.generate_code('TS', seed_editor, 'Ali')  # Creates TS001

        with pytest.raises(ValueError, match="already in use"):
            CodeService.lock_code_manual('TS001', 'TS', seed_editor)

    def test_cannot_unlock_non_locked_code(self, seed_product_line):
        """Real DB: unlocking a code that isn't locked raises ValueError"""
        from services.code_service import CodeService

        with pytest.raises(ValueError, match="is not locked"):
            CodeService.unlock_code('TS999')


# ---------------------------------------------------------------------------
# Integration: Design model
# ---------------------------------------------------------------------------

class TestIntegrationDesign:

    def test_approve_locks_code(self, seed_product_line, seed_editor, seed_reviewer):
        """Real DB: approving a design should lock the code"""
        from services.code_service import CodeService
        from models.design import Design

        code, design = CodeService.generate_code('TS', seed_editor, 'Ali')

        # Add files so it's a valid design
        design.mockup_file_ids = ['file1']
        design.print_file_ids = ['pfile1']
        design.save()

        result = design.approve(seed_reviewer, 'Nazi')

        assert result
        assert design.status == DesignStatus.APPROVED

        # Code should be locked after approval
        locked = _get_locked(code)
        assert locked is not None
        assert locked['is_manual'] == 0  # auto-locked

        # Code should NOT be available again
        from services.code_service import CodeService
        assert not CodeService.is_code_available(code)

    def test_reject_frees_code(self, seed_product_line, seed_editor, seed_reviewer):
        """Real DB: rejecting should rename code with _REJ_ and free the slot"""
        from services.code_service import CodeService
        from models.design import Design

        code, design = CodeService.generate_code('TS', seed_editor, 'Ali')
        design.mockup_file_ids = ['file1']
        design.print_file_ids = ['pfile1']
        design.save()

        result = design.reject(seed_reviewer, 'Nazi')

        assert result
        assert design.status == DesignStatus.REJECTED

        # Original code should be FREE
        assert CodeService.is_code_available(code)

        # Verify DB has the _REJ_ renamed entry
        conn = get_test_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT * FROM designs WHERE code LIKE %s",
            (f"{code}_REJ_%",)
        )
        rej_row = cursor.fetchone()
        cursor.close()
        conn.close()

        assert rej_row is not None
        assert rej_row['status'] == 'rejected'

    def test_approve_race_condition(self, seed_product_line, seed_editor, seed_reviewer):
        """Real DB: only one reviewer should win the approval race"""
        from services.code_service import CodeService
        from models.design import Design

        code, design = CodeService.generate_code('TS', seed_editor, 'Ali')
        design.mockup_file_ids = ['file1']
        design.print_file_ids = ['pfile1']
        design.save()

        # Simulate two reviewers approving simultaneously
        design1 = Design.get_by_code(code)
        design2 = Design.get_by_code(code)

        result1 = design1.approve(seed_reviewer, 'Nazi')
        result2 = design2.approve(9999, 'Other Reviewer')

        # Only one should succeed
        assert result1
        assert not result2

    def test_delete_design_flow(self, seed_product_line, seed_editor, seed_reviewer):
        """Real DB: full approve → delete flow"""
        from services.code_service import CodeService
        from models.design import Design

        code, design = CodeService.generate_code('TS', seed_editor, 'Ali')
        design.mockup_file_ids = ['file1']
        design.print_file_ids = ['pfile1']
        design.save()
        design.approve(seed_reviewer, 'Nazi')

        # Now delete (simulating design_management delete flow)
        conn = get_test_connection()
        cursor = conn.cursor()

        # Free locked code
        cursor.execute("DELETE FROM designs_locked_codes WHERE code = %s", (code,))

        # Rename design to _DEL_
        import time
        del_code = f"{code}_DEL_{int(time.time())}"
        cursor.execute(
            "UPDATE designs SET code = %s, status = 'deleted' WHERE code = %s",
            (del_code, code)
        )
        conn.commit()
        cursor.close()
        conn.close()

        # Code should be free again
        assert CodeService.is_code_available(code)

    def test_cleanup_orphaned_designs(self, seed_product_line, seed_editor):
        """Real DB: orphaned pending designs should be cleaned up"""
        from services.code_service import CodeService

        conn = get_test_connection()
        cursor = conn.cursor()

        # Insert an old orphaned design (no files, older than 24h)
        cursor.execute("""
            INSERT INTO designs
            (code, product_line_id, status, editor_user_id,
             editor_name, mockup_file_ids, print_file_ids, created_at)
            VALUES ('TS001', %s, 'pending', %s, 'Ali', '[]', '[]',
                    NOW() - INTERVAL 25 HOUR)
        """, (seed_product_line, seed_editor))
        conn.commit()
        cursor.close()
        conn.close()

        CodeService.cleanup_orphaned_designs()

        # Should be gone
        assert _get_design('TS001') is None

    def test_cleanup_keeps_recent_orphans(self, seed_product_line, seed_editor):
        """Real DB: orphans less than 24h old should be kept"""
        from services.code_service import CodeService

        conn = get_test_connection()
        cursor = conn.cursor()

        # Insert a RECENT orphaned design
        cursor.execute("""
            INSERT INTO designs
            (code, product_line_id, status, editor_user_id,
             editor_name, mockup_file_ids, print_file_ids, created_at)
            VALUES ('TS001', %s, 'pending', %s, 'Ali', '[]', '[]', NOW())
        """, (seed_product_line, seed_editor))
        conn.commit()
        cursor.close()
        conn.close()

        CodeService.cleanup_orphaned_designs()

        # Should still be there
        assert _get_design('TS001') is not None

    def test_cleanup_keeps_designs_with_files(self, seed_product_line, seed_editor):
        """Real DB: pending designs WITH files should never be cleaned up"""
        from services.code_service import CodeService

        conn = get_test_connection()
        cursor = conn.cursor()

        # Old design BUT has files
        cursor.execute("""
            INSERT INTO designs
            (code, product_line_id, status, editor_user_id,
             editor_name, mockup_file_ids, print_file_ids, created_at)
            VALUES ('TS001', %s, 'pending', %s, 'Ali',
                    '["file1"]', '["pfile1"]',
                    NOW() - INTERVAL 25 HOUR)
        """, (seed_product_line, seed_editor))
        conn.commit()
        cursor.close()
        conn.close()

        CodeService.cleanup_orphaned_designs()

        # Should still be there
        assert _get_design('TS001') is not None