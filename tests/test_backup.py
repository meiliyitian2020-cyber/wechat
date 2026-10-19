from pathlib import Path
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
    target=root/'contact.db'; target.symlink_to(db)
    with pytest.raises(BackupError): BackupManager(tmp_path/'backups').create(root,['contact.db'],lambda:None)

def test_no_backup_inside_account(source):
    root,db=source
    with pytest.raises(BackupError): BackupManager(root/'backups').create(root,[db.relative_to(root)],lambda:None)
