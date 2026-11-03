"""Injected-failure matrix for replace+backup rollback (RB-05).

All 28 cases must leave the original bytes in the source, the backup, or a
.cropper-recovered file. Case corrupt#2+all-later-fail is the blocker: verifying
the replaced file fails, restoring the original fails, and later cleanup fails.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
import io

import app_settings
import stage_files as stages


def png(color='red'):
    stream = io.BytesIO()
    Image.new('RGB', (16, 16), color).save(stream, 'PNG')
    return stream.getvalue()


WRITE_MODES = ('success', 'fail-backup', 'fail-output', 'corrupt#2')
LATER_MODES = (
    'none',
    'restore-fail',
    'recovered-fail',
    'unlink-backup-fail',
    'restore+recovered-fail',
    'restore+unlink-fail',
    'all-later-fail',
)
CASES = [f'{write}+{later}' for write in WRITE_MODES for later in LATER_MODES]


class Rb05MatrixTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        app_settings.configure_runtime(self.folder.name)
        self.addCleanup(lambda: setattr(app_settings, '_data_dir', None))

    def original_survives(self, source: Path, backup: Path, original: bytes) -> bool:
        locations = []
        if source.exists():
            try:
                locations.append(source.read_bytes())
            except OSError:
                pass
        if backup.exists():
            try:
                locations.append(backup.read_bytes())
            except OSError:
                pass
        parent = source.parent
        for recovered in parent.glob(source.name + '.cropper-recovered*'):
            try:
                locations.append(recovered.read_bytes())
            except OSError:
                pass
        return any(item == original for item in locations)

    def run_case(self, case: str) -> None:
        write_mode, later = case.split('+', 1)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / stages.CROP_QUEUE / 'photo.png'
            source.parent.mkdir(parents=True)
            original = png('red')
            payload = png('blue')
            source.write_bytes(original)
            backup = root / stages.BACKUPS / 'photo.png'
            settings = {
                'filename_template': '{name}{suffix}{ext}',
                'conflict_policy': 'skip',
                'replace_original': True,
                'backup_originals': True,
                'output_folder': '',
            }
            original_atomic = stages._atomic_create
            original_unlink = Path.unlink

            def atomic_wrapper(target, data, overwrite=False):
                path = Path(target)
                is_recovered = '.cropper-recovered' in path.name
                is_restore = overwrite and data == original
                is_backup_write = (not overwrite) and data == original and not is_recovered
                is_output_write = overwrite and data == payload
                if is_backup_write and write_mode == 'fail-backup':
                    raise OSError('fail backup write')
                if is_output_write and write_mode == 'fail-output':
                    raise OSError('fail output write')
                if is_restore and later in {
                    'restore-fail', 'restore+recovered-fail', 'restore+unlink-fail', 'all-later-fail',
                }:
                    raise OSError('restore fail')
                if is_recovered and later in {
                    'recovered-fail', 'restore+recovered-fail', 'all-later-fail',
                }:
                    raise OSError('recovered fail')
                created = original_atomic(target, data, overwrite=overwrite)
                if is_output_write and write_mode == 'corrupt#2':
                    stages.windows_long_path(path).write_bytes(b'CORRUPT')
                return created

            def unlink_wrapper(self, missing_ok=False):
                if stages.is_backup_path(self) and later in {
                    'unlink-backup-fail', 'restore+unlink-fail', 'all-later-fail',
                }:
                    raise OSError('unlink backup fail')
                return original_unlink(self, missing_ok=missing_ok)

            with patch.object(stages, '_settings', return_value=settings), \
                 patch.object(stages, '_atomic_create', atomic_wrapper), \
                 patch.object(Path, 'unlink', unlink_wrapper):
                try:
                    stages.file_results([source], [payload], 'restore')
                except Exception:
                    pass
            self.assertTrue(
                self.original_survives(source, backup, original),
                f'{case} lost the original (source={source.exists()} backup={backup.exists()})',
            )
            if write_mode == 'corrupt#2' and later in {
                'restore-fail', 'restore+recovered-fail', 'restore+unlink-fail', 'all-later-fail',
            }:
                self.assertTrue(backup.exists(), f'{case} deleted the backup before restore was verified')
                if later not in {'restore-fail', 'restore+unlink-fail'} or later == 'all-later-fail':
                    # Recovered may fail in recovered-fail modes; backup must still hold original.
                    self.assertEqual(backup.read_bytes(), original)

    def test_matrix_has_twenty_eight_named_cases(self):
        self.assertEqual(len(CASES), 28)
        self.assertIn('corrupt#2+all-later-fail', CASES)

    def test_all_twenty_eight_cases_are_safe(self):
        for case in CASES:
            with self.subTest(case=case):
                self.run_case(case)
