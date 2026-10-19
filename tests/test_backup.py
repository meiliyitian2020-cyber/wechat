from pathlib import Path
import os
import pytest
from editor.backup import BackupManager, BackupError

@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'account'
    db = root / 'db_storage' / 'contact' / 'contact.db'
    db.parent.mkdir(parents=True)
    db.write_bytes(b'encrypted-database-placeholder')
    return root, db

def test_backup_preserves_db_wal_shm_presence_and_source(source, tmp_path):
    root, db = source
    Path(str(db)+'-wal').write_bytes(b'wal')
    before = db.read_bytes()
    bundle = BackupManager(tmp_path/'backups').create(root, [db.relative_to(root)], lambda:None)
    manifest = BackupManager.verify(bundle)
    assert manifest['files']['db_storage/contact/contact.db-shm'] is None
    assert (bundle/'files/db_storage/contact/contact.db-wal').read_bytes() == b'wal'
    assert db.read_bytes() == before
    assert not list((tmp_path/'backups').glob('.incomplete-*'))

def test_detect_tampered_backup(source, tmp_path):
    root, db = source
    bundle = BackupManager(tmp_path/'backups').create(root, [db.relative_to(root)], lambda:None)
    (bundle/'files'/db.relative_to(root)).write_bytes(b'tampered')
    with pytest.raises(BackupError): BackupManager.verify(bundle)

def test_source_changed_during_copy_aborts(source, tmp_path):
    root, db = source
    calls = []
    def guard():
        calls.append(1)
        if len(calls)==2: Path(str(db)+'-wal').write_bytes(b'new wal')
    with pytest.raises(BackupError):
        BackupManager(tmp_path/'backups').create(root, [db.relative_to(root)], guard)
    assert list((tmp_path/'backups').iterdir()) == []

def test_process_restart_aborts_and_removes_partial_copy(source, tmp_path):
    root, db = source
    calls=[]
    def guard():
        calls.append(1)
        if len(calls)==2: raise RuntimeError('running')
    with pytest.raises(RuntimeError): BackupManager(tmp_path/'backups').create(root,[db.relative_to(root)],guard)
    assert not list((tmp_path/'backups').iterdir())

@pytest.mark.parametrize('relative',['../contact.db','message_0.db','/contact.db'])
def test_reject_unsafe_targets(source,tmp_path,relative):
    root,_=source
    with pytest.raises(BackupError): BackupManager(tmp_path/'backups').create(root,[relative],lambda:None)

def test_reject_symlink(source,tmp_path):
    root,db=source
    target=root/'contact.db'
    try:
        target.symlink_to(db)
    except OSError as exc:
        if os.name == 'nt' and getattr(exc, 'winerror', None) == 1314:
            pytest.skip('Windows 当前用户没有创建符号链接的权限；仅跳过真实链接集成测试')
        raise
    with pytest.raises(BackupError): BackupManager(tmp_path/'backups').create(root,['contact.db'],lambda:None)

def test_no_backup_inside_account(source):
    root,db=source
    with pytest.raises(BackupError): BackupManager(root/'backups').create(root,[db.relative_to(root)],lambda:None)


def test_symlink_guard_requires_no_creation_privilege(source, tmp_path, monkeypatch):
    """Exercise fail-closed guard even when Windows denies link creation."""
    root, db = source
    relative = db.relative_to(root)
    directory = root / 'db_storage'
    original = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink',
                        lambda path: path == directory or original(path))
    with pytest.raises(BackupError, match='符号链接'):
        BackupManager(tmp_path/'backups').create(root, [relative], lambda:None)
    assert not (tmp_path/'backups').exists()
    assert db.read_bytes() == b'encrypted-database-placeholder'
