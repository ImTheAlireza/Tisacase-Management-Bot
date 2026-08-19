"""
Regression tests for the zip-slip guard in RestoreService.

Bug being guarded: restore_public_files used to join the entry name onto
PUBLIC_DIR without validating `..`, so a crafted backup ZIP containing
`public/../../etc/...` could write files outside the public directory.
"""
import os
import zipfile

# config.settings exits at import time when these env vars are missing, so
# provide dummies before importing anything that pulls in the settings module.
os.environ.setdefault('MAIN_BOT_TOKEN', '123456:test-token')
os.environ.setdefault('MAIN_ALIREZA_CHAT_ID', '111')
os.environ.setdefault('MAIN_NAZI_CHAT_ID', '222')
os.environ.setdefault('MAIN_LOG_GROUP_ID', '333')
os.environ.setdefault('MAIN_DB_HOST', 'localhost')
os.environ.setdefault('MAIN_DB_USER', 'root')
os.environ.setdefault('MAIN_DB_PASSWORD', '')
os.environ.setdefault('MAIN_DB_NAME', 'tisa_test')

import pytest

from services.restore_service import RestoreService


class TestIsSafeRelativePath:

    @pytest.mark.parametrize("path", [
        '../evil.txt',
        'a/../../evil.txt',
        '/absolute/path.txt',
        '..\\evil.txt',
        '..',
        '',
    ])
    def test_rejects_unsafe_paths(self, path):
        assert RestoreService.is_safe_relative_path(path) is False

    @pytest.mark.parametrize("path", [
        'file.txt',
        'sub/file.txt',
        'sub/dir/file.sql',
    ])
    def test_accepts_safe_paths(self, path):
        assert RestoreService.is_safe_relative_path(path) is True


class TestRestorePublicFiles:

    def _make_zip(self, tmp_path, entries):
        zip_path = str(tmp_path / 'backup.zip')
        with zipfile.ZipFile(zip_path, 'w') as z:
            for name, data in entries.items():
                z.writestr(name, data)
        return zip_path

    def test_rejects_zip_slip_entry(self, tmp_path, monkeypatch):
        public_dir = tmp_path / 'public'
        public_dir.mkdir()
        monkeypatch.setattr('services.restore_service.PUBLIC_DIR', str(public_dir))

        zip_path = self._make_zip(tmp_path, {
            'public/../evil.txt': 'pwned',
            'public/ok.txt': 'fine',
        })

        result = RestoreService.restore_public_files(zip_path)

        assert result['success'] is True
        # Only the safe entry is restored; the traversal entry is skipped.
        assert result['files_restored'] == 1
        assert (public_dir / 'ok.txt').read_text() == 'fine'
        # Nothing may be written outside the public directory.
        assert not (tmp_path / 'evil.txt').exists()

    def test_restores_safe_entry(self, tmp_path, monkeypatch):
        public_dir = tmp_path / 'public'
        public_dir.mkdir()
        monkeypatch.setattr('services.restore_service.PUBLIC_DIR', str(public_dir))

        zip_path = self._make_zip(tmp_path, {'public/hello.txt': 'hello world'})

        result = RestoreService.restore_public_files(zip_path)

        assert result['success'] is True
        assert result['files_restored'] == 1
        assert (public_dir / 'hello.txt').read_text() == 'hello world'

    def test_no_public_entries_is_success(self, tmp_path, monkeypatch):
        public_dir = tmp_path / 'public'
        public_dir.mkdir()
        monkeypatch.setattr('services.restore_service.PUBLIC_DIR', str(public_dir))

        zip_path = self._make_zip(tmp_path, {'other/file.txt': 'x'})

        result = RestoreService.restore_public_files(zip_path)

        assert result['success'] is True
        assert result['files_restored'] == 0
