import json
import os
from pathlib import Path
from contextlib import closing
import secrets
import pytest
from sqlcipher3 import dbapi2 as sql
from editor.offline import OfflineAvatar, OfflineError, Context, WindowsFile, hash_bytes
from editor.cipher_stage import open_cipher
from editor.observe import read_current
from editor.core import Store, State

class FileLock:
    """Portable file I/O for fault tests; Windows exclusive access tested separately."""
    def __init__(self,path): self.path=path
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self): return self.path.read_bytes()
    def write(self,data): self.path.write_bytes(data)

@pytest.fixture
def fixture(tmp_path):
    root=tmp_path/'account'
    source=root/'db_storage/head_image/head_image.db';source.parent.mkdir(parents=True)
    key=secrets.token_bytes(32)
    with closing(sql.connect(source)) as db:
        db.execute('PRAGMA key = "x\''+key.hex()+'\'"')
        db.execute('CREATE TABLE head_image (username TEXT PRIMARY KEY, image_buffer BLOB, md5 TEXT, update_time INTEGER)')
        db.executemany('INSERT INTO head_image VALUES (?,?,?,?)',[('friend',b'old','',0),('untouched',b'other','',0)])
        db.commit()
    context=Context(source,root,'account',tmp_path/'Weixin.exe',key)
    service=OfflineAvatar(tmp_path/'backups',_lock=FileLock,_guard=lambda:None,_validate=lambda c:None,_version=lambda c:None,_read_version=lambda c:None)
    return context,service

def avatar(context,friend='friend'):
    with closing(open_cipher(context.source,context.key)) as db:
        row=db.execute('SELECT image_buffer FROM head_image WHERE username=?',(friend,)).fetchone()
        return row[0] if row else None

def test_actual_encrypted_file_write_backup_and_target_only(fixture):
    context,service=fixture; before=context.source.read_bytes()
    bundle=Path(service.apply(context,'friend',b'custom'))
    assert avatar(context)==b'custom'
    assert avatar(context,'untouched')==b'other'
    assert (bundle/'original.enc').read_bytes()==before
    record=json.loads((bundle/'operation.json').read_text())
    assert record['state']=='committed'
    assert record['replacement_hash']==hash_bytes(context.source.read_bytes())
    assert context.key.hex() not in (bundle/'operation.json').read_text()

@pytest.mark.parametrize('fault_at',['before_write','after_write'])
def test_write_failure_rolls_back_exact_encrypted_bytes(fixture,fault_at):
    context,service=fixture; before=context.source.read_bytes()
    def fail(phase):
        if phase==fault_at: raise RuntimeError('injected')
    with pytest.raises(OfflineError,match='已恢复'):
        service.apply(context,'friend',b'new',_fault=fail)
    assert context.source.read_bytes()==before
    assert avatar(context)==b'old'
    record=json.loads(next(service.backup_root.glob('*/operation.json')).read_text())
    assert record['state']=='rolled_back'

def test_restore_invalidates_instead_of_writing_obsolete_avatar(fixture):
    context,service=fixture
    service.apply(context,'friend',b'custom')
    service.invalidate(context,'friend')
    assert avatar(context) is None
    assert avatar(context,'untouched')==b'other'

def test_sidecar_blocks_write_without_deleting_it(fixture):
    context,service=fixture; before=context.source.read_bytes()
    wal=Path(str(context.source)+'-wal');wal.write_bytes(b'preserve')
    with pytest.raises(OfflineError,match='WAL'):
        service.apply(context,'friend',b'new')
    assert wal.read_bytes()==b'preserve'
    assert context.source.read_bytes()==before

def test_running_process_prevents_any_write(fixture):
    context,service=fixture;before=context.source.read_bytes()
    def running(): raise OfflineError('running')
    service.guard=running
    with pytest.raises(OfflineError): service.apply(context,'friend',b'new')
    assert context.source.read_bytes()==before
    assert not service.backup_root.exists()

def test_recovery_of_known_interrupted_image(fixture):
    context,service=fixture;before=context.source.read_bytes()
    bundle=Path(service.apply(context,'friend',b'new'))
    path=bundle/'operation.json';record=json.loads(path.read_text());record['state']='writing';path.write_text(json.dumps(record))
    assert service.recover('account',context=context)==1
    assert context.source.read_bytes()==before

def test_recovery_refuses_changes_from_other_source(fixture):
    context,service=fixture
    bundle=Path(service.apply(context,'friend',b'new'))
    path=bundle/'operation.json';record=json.loads(path.read_text());record['state']='writing';path.write_text(json.dumps(record))
    data=context.source.read_bytes()+b'foreign-change';context.source.write_bytes(data)
    with pytest.raises(OfflineError,match='无法自动区分'): service.recover('account',context=context)
    assert context.source.read_bytes()==data
    with pytest.raises(OfflineError,match='未完成'): service.apply(context,'friend',b'another')

def test_stable_observation_and_expiration_detection(fixture,tmp_path):
    context,service=fixture
    service.apply(context,'friend',b'new')
    store=Store(tmp_path/'settings')
    store.save('account','friend','name',b'old',None,b'new')
    store.record_applied('account','friend','backup')
    seen=read_current(context,['friend'],_validate=lambda c:None)
    assert seen['friend']==b'new'
    assert store.verify_all(lambda a,f:seen.get(f))[0][2]==State.UNKNOWN
    service.apply(context,'friend',b'synced-real-avatar')
    seen=read_current(context,['friend'],_validate=lambda c:None)
    assert store.verify_all(lambda a,f:seen.get(f))[0][2]==State.INVALID

def test_observation_sees_latest_wal_without_touching_source(fixture):
    context,service=fixture
    db=open_cipher(context.source,context.key)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute('UPDATE head_image SET image_buffer=? WHERE username=?',(b'latest-wal','friend'));db.commit()
        original=context.source.read_bytes();wal=Path(str(context.source)+'-wal');before_wal=wal.read_bytes()
        seen=read_current(context,['friend'],_validate=lambda c:None)
        assert seen['friend']==b'latest-wal'
        assert context.source.read_bytes()==original and wal.read_bytes()==before_wal
    finally: db.close()

@pytest.mark.skipif(os.name!='nt',reason='Windows CreateFile exclusive handle integration')
def test_windows_handle_writes_and_excludes_second_opener(fixture):
    context,service=fixture;service.lock=WindowsFile
    with WindowsFile(context.source) as locked:
        assert locked.read()
        with pytest.raises((OSError,OfflineError)):
            with WindowsFile(context.source): pass
    service.apply(context,'friend',b'windows-write')
    assert avatar(context)==b'windows-write'

def test_gui_apply_uses_real_backend_and_persists_success(fixture,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from editor.gui import Window
    import editor.gui as gui
    def unavailable(): raise RuntimeError('test-only')
    monkeypatch.setattr(gui,'discover',unavailable)
    monkeypatch.setattr(gui,'read_current',lambda context,friends: {f:b'old' for f in friends})
    monkeypatch.setattr(QMessageBox,'information',lambda *args: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox,'warning',lambda *args: (_ for _ in ()).throw(AssertionError('unexpected error dialog')))
    app=QApplication.instance() or QApplication([])
    context,service=fixture
    store=Store(tmp_path/'ui-settings');window=Window(store)
    window.offline=service
    window.accounts.addItem('account','account')
    window.loaded(('account',[{'username':'friend','nick_name':'测试好友','remark':''}],{'friend':b'old'},context))
    window.items.setCurrentRow(0);window.pending_avatar=b'custom'
    monkeypatch.setattr(window,'confirm_write',lambda text:True)
    window.apply()
    assert avatar(context)==b'custom'
    row=store.rows()[0]
    assert row['desired_avatar']==b'custom' and row['last_applied']
    assert row['state']==State.UNKNOWN.value
    assert '客户端' in row['scope']
    assert window.close();app.processEvents()


def test_gui_cancel_does_not_touch_database(fixture,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication
    import editor.gui as gui
    def unavailable(): raise RuntimeError('test-only')
    monkeypatch.setattr(gui,'discover',unavailable)
    context,service=fixture;before=context.source.read_bytes()
    app=QApplication.instance() or QApplication([])
    window=gui.Window(Store(tmp_path/'cancel-settings'));window.offline=service
    window.accounts.addItem('account','account')
    window.loaded(('account',[{'username':'friend','nick_name':'测试好友','remark':''}],{},context))
    window.items.setCurrentRow(0);window.pending_avatar=b'custom'
    monkeypatch.setattr(window,'confirm_write',lambda text:False)
    window.apply()
    assert context.source.read_bytes()==before
    assert not service.backup_root.exists()
    assert window.close();app.processEvents()

def test_new_version_probe_checks_real_copy_without_original_writes(fixture,tmp_path,monkeypatch):
    import editor.offline as offline
    context,_=fixture;context.detected_version='4.1.15.65'
    monkeypatch.setattr(offline,'version',lambda path:'4.1.15.65')
    service=OfflineAvatar(tmp_path/'candidate-backups',_lock=FileLock,_guard=lambda:None)
    original=context.source.read_bytes()
    report=service.probe(context,'friend',b'private-custom-avatar')
    assert report['wechat_version']=='4.1.15.65'
    assert report['source_unchanged'] and report['roundtrip_ok'] and report['non_target_rows_unchanged']
    assert not report['original_database_written']
    assert not report['client_rendering_verified']
    assert context.source.read_bytes()==original
    assert not service.backup_root.exists()
    text=json.dumps(report)
    for private in [context.account, str(context.source), context.key.hex(), 'private-custom-avatar']:
        assert private not in text
    # A successful copy test is not permission to change the original DB.
    with pytest.raises(OfflineError,match='尚未开放'): service.apply(context,'friend',b'new')
    assert context.source.read_bytes()==original

@pytest.mark.parametrize('operation',['probe','apply'])
def test_unrecognized_trigger_is_rejected_before_source_changes(fixture,operation):
    from editor.compatibility import CompatibilityError
    context,service=fixture
    with closing(open_cipher(context.source,context.key)) as db:
        db.execute("CREATE TRIGGER side_effect AFTER UPDATE ON head_image BEGIN UPDATE head_image SET image_buffer=x'00' WHERE username='untouched'; END")
        db.commit()
    original=context.source.read_bytes()
    with pytest.raises(CompatibilityError,match='触发器'):
        getattr(service,operation)(context,'friend',b'new')
    assert context.source.read_bytes()==original
    assert avatar(context,'untouched')==b'other'

def test_old_loaded_context_is_not_reused_after_wechat_update(fixture,tmp_path,monkeypatch):
    import editor.offline as offline
    context,_=fixture
    monkeypatch.setattr(offline,'version',lambda path:'4.1.15.65')
    service=OfflineAvatar(tmp_path/'updated-backups',_lock=FileLock,_guard=lambda:None)
    original=context.source.read_bytes()
    with pytest.raises(OfflineError,match='版本变化'): service.probe(context,'friend',b'new')
    with pytest.raises(OfflineError,match='版本变化'): service.apply(context,'friend',b'new')
    assert context.source.read_bytes()==original

def test_candidate_gui_can_probe_but_refuses_original_writes(fixture,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication,QMessageBox
    import editor.gui as gui
    def unavailable(): raise RuntimeError('test-only')
    monkeypatch.setattr(gui,'discover',unavailable)
    monkeypatch.setattr(QMessageBox,'question',lambda *args: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox,'exec',lambda self:0)
    warnings=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *args: warnings.append(args[2]))
    context,service=fixture;context.detected_version='4.1.15.65'
    app=QApplication.instance() or QApplication([])
    store=Store(tmp_path/'candidate-ui');window=gui.Window(store);window.offline=service
    window.accounts.addItem('account','account')
    window.loaded(('account',[{'username':'friend','nick_name':'测试好友','remark':''}],{},context))
    window.items.setCurrentRow(0);window.pending_avatar=b'candidate-avatar'
    before=context.source.read_bytes()
    window.probe()
    reports=list(store.path.parent.glob('compatibility-*.json'))
    assert len(reports)==1
    assert json.loads(reports[0].read_text(encoding='utf-8'))['roundtrip_ok']
    assert not warnings
    window.apply()
    assert warnings and '尚未开放' in warnings[-1]
    assert context.source.read_bytes()==before
    assert not service.backup_root.exists()
    assert window.close();app.processEvents()
