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

@pytest.fixture(params=['4.1.15.56','4.1.15.65'])
def candidate(fixture,tmp_path,monkeypatch,request):
    import editor.offline as offline
    context,_=fixture;context.detected_version=request.param
    monkeypatch.setattr(offline,'version',lambda path:request.param)
    service=OfflineAvatar(tmp_path/'trial-backups',_lock=FileLock,_guard=lambda:None)
    return context,service

def test_candidate_trial_requires_actual_memory_probe(candidate):
    context,service=candidate;original=context.source.read_bytes()
    with pytest.raises(OfflineError,match='副本检查'):
        service.apply(context,'friend',b'new',_candidate_trial=True)
    assert context.source.read_bytes()==original

def test_candidate_trial_writes_only_probed_data_once(candidate):
    context,service=candidate;original=context.source.read_bytes()
    service.probe(context,'friend',b'new')
    bundle=Path(service.apply(context,'friend',b'new',_candidate_trial=True))
    assert avatar(context)==b'new' and avatar(context,'untouched')==b'other'
    assert (bundle/'original.enc').read_bytes()==original
    record=json.loads((bundle/'operation.json').read_text())
    assert record['candidate_trial'] and record['wechat_version']==context.detected_version
    assert record['state']=='committed'
    with pytest.raises(OfflineError,match='副本检查'):
        service.apply(context,'friend',b'new',_candidate_trial=True)

@pytest.mark.parametrize('change',['image','expired','source'])
def test_candidate_trial_rejects_stale_or_mismatched_proof(candidate,change):
    context,service=candidate
    service.probe(context,'friend',b'new')
    desired=b'new'
    if change=='image': desired=b'different'
    elif change=='expired': service._probes[(id(context),'friend',False)]['time']-=301
    else:
        with closing(open_cipher(context.source,context.key)) as db:
            db.execute('UPDATE head_image SET image_buffer=? WHERE username=?',(b'external-sync','untouched'));db.commit()
    before=context.source.read_bytes()
    with pytest.raises(OfflineError): service.apply(context,'friend',desired,_candidate_trial=True)
    assert context.source.read_bytes()==before

def test_candidate_restore_needs_its_own_delete_probe(candidate):
    context,service=candidate
    service.probe(context,'friend',b'new')
    with pytest.raises(OfflineError): service.invalidate(context,'friend',_candidate_trial=True)
    report=service.probe(context,'friend',None,_invalidate=True)
    assert report['operation']=='invalidate'
    service.invalidate(context,'friend',_candidate_trial=True)
    assert avatar(context) is None and avatar(context,'untouched')==b'other'

def test_candidate_interrupted_trial_can_recover_same_version(candidate):
    context,service=candidate;original=context.source.read_bytes()
    service.probe(context,'friend',b'new')
    bundle=Path(service.apply(context,'friend',b'new',_candidate_trial=True))
    jp=bundle/'operation.json';record=json.loads(jp.read_text());record['state']='writing';jp.write_text(json.dumps(record))
    assert service.recover('account',context=context)==1
    assert context.source.read_bytes()==original

@pytest.mark.parametrize('operation',['probe','apply'])
def test_virtual_avatar_table_is_not_an_adapted_schema(fixture,operation):
    from editor.compatibility import CompatibilityError
    context,service=fixture
    with closing(open_cipher(context.source,context.key)) as db:
        db.execute('DROP TABLE head_image')
        db.execute('CREATE VIRTUAL TABLE head_image USING fts5(username,image_buffer,md5,update_time)')
        db.execute('INSERT INTO head_image VALUES(?,?,?,?)',('friend',b'old','',0));db.commit()
    before=context.source.read_bytes()
    with pytest.raises(CompatibilityError,match='普通数据表'):
        getattr(service,operation)(context,'friend',b'new')
    assert context.source.read_bytes()==before

def test_candidate_gui_explicit_trial_button_reaches_guarded_write(fixture,tmp_path,monkeypatch):
    from PySide6.QtWidgets import QApplication,QMessageBox
    import editor.gui as gui
    def unavailable(): raise RuntimeError('test-only')
    monkeypatch.setattr(gui,'discover',unavailable)
    monkeypatch.setattr(QMessageBox,'question',lambda *args: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox,'exec',lambda self:0)
    monkeypatch.setattr(QMessageBox,'clickedButton',lambda box: next((b for b in box.buttons() if b.text()=='仅本次试验应用'),None))
    monkeypatch.setattr(QMessageBox,'information',lambda *args: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox,'warning',lambda *args: (_ for _ in ()).throw(AssertionError('unexpected dialog')))
    context,service=fixture;context.detected_version='4.1.15.65'
    app=QApplication.instance() or QApplication([])
    store=Store(tmp_path/'trial-ui');window=gui.Window(store);window.offline=service
    window.accounts.addItem('account','account')
    window.loaded(('account',[{'username':'friend','nick_name':'测试好友','remark':''}],{},context))
    window.items.setCurrentRow(0);window.pending_avatar=b'trial-from-ui'
    window.probe()
    assert avatar(context)==b'trial-from-ui'
    assert store.rows()[0]['last_applied']
    assert store.rows()[0]['state']==State.UNKNOWN.value
    assert window.close();app.processEvents()

def residual_wal(context):
    """Create an offline snapshot with committed data only in WAL, no live handle."""
    db=open_cipher(context.source,context.key)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute('UPDATE head_image SET image_buffer=? WHERE username=?',(b'wal-latest-target','friend'))
        db.execute('UPDATE head_image SET image_buffer=? WHERE username=?',(b'wal-latest-other','untouched'))
        db.commit()
        data={suffix:Path(str(context.source)+suffix).read_bytes() for suffix in ('','-wal','-shm')}
    finally: db.close()
    # Recreate a PERSIST_WAL-like offline file set after the SQLite connection ends.
    for suffix,value in data.items(): Path(str(context.source)+suffix).write_bytes(value)
    return data

def current_set(context):
    return {suffix:Path(str(context.source)+suffix).read_bytes() if Path(str(context.source)+suffix).exists() else None
            for suffix in ('','-wal','-shm')}

def test_residual_wal_probe_preserves_all_original_files_and_latest_rows(fixture):
    context,service=fixture;original=residual_wal(context)
    report=service.probe(context,'friend',b'custom')
    assert report['wal_present'] and report['shm_present']
    assert report['committed_wal_frames']>0 and report['source_unchanged']
    assert current_set(context)==original


def test_wal_writeback_keeps_other_contacts_latest_commit_and_full_backup(fixture):
    context,service=fixture;original=residual_wal(context)
    bundle=Path(service.apply(context,'friend',b'custom'))
    assert Path(str(context.source)+'-wal').read_bytes()==b''
    assert Path(str(context.source)+'-shm').read_bytes()==b''
    assert (bundle/'original.enc').read_bytes()==original['']
    assert (bundle/'wal.enc').read_bytes()==original['-wal']
    assert (bundle/'shm.bin').read_bytes()==original['-shm']
    assert avatar(context)==b'custom'
    assert avatar(context,'untouched')==b'wal-latest-other'

@pytest.mark.parametrize('phase',['after_main','after_wal','after_shm'])
def test_mid_file_set_failure_restores_main_wal_and_shm_exactly(fixture,phase):
    context,service=fixture;original=residual_wal(context)
    def fail(at):
        if at==phase: raise RuntimeError('injected multi-file fault')
    with pytest.raises(OfflineError,match='已恢复'):
        service.apply(context,'friend',b'custom',_fault=fail)
    assert current_set(context)==original
    record=json.loads(next(service.backup_root.glob('*/operation.json')).read_text())
    assert record['state']=='rolled_back'
    # Recovery must preserve the previously committed data that lived in WAL.
    assert avatar(context)==b'wal-latest-target'
    assert avatar(context,'untouched')==b'wal-latest-other'

@pytest.mark.parametrize('empty_wal',[True,False])
def test_empty_wal_or_shm_only_is_handled_without_manual_deletion(fixture,empty_wal):
    context,service=fixture
    if empty_wal: Path(str(context.source)+'-wal').write_bytes(b'')
    Path(str(context.source)+'-shm').write_bytes(b'old-index'*4096)
    original=current_set(context)
    report=service.probe(context,'friend',b'custom')
    assert report['committed_wal_frames']==0
    assert current_set(context)==original
    service.apply(context,'friend',b'custom')
    assert Path(str(context.source)+'-shm').read_bytes()==b''
    assert avatar(context)==b'custom'

@pytest.mark.parametrize('kind',['header','checksum','hmac','truncated'])
def test_invalid_wal_is_rejected_without_changing_any_original_file(fixture,kind):
    import struct
    from editor.wal import checksum,FRAME
    context,service=fixture;residual_wal(context)
    path=Path(str(context.source)+'-wal');data=bytearray(path.read_bytes())
    if kind=='header': data[12]^=1
    elif kind=='truncated': data=data[:-100]
    else:
        data[32+24+100]^=1
        if kind=='hmac':
            magic=struct.unpack('>I',data[:4])[0];endian='>' if magic&1 else '<'
            rolling=struct.unpack('>II',data[24:32])
            for offset in range(32,len(data),FRAME):
                rolling=checksum(bytes(data[offset:offset+8])+bytes(data[offset+24:offset+FRAME]),rolling,endian)
                data[offset+16:offset+24]=struct.pack('>II',*rolling)
    path.write_bytes(data);original=current_set(context)
    with pytest.raises(OfflineError,match='WAL'):
        service.probe(context,'friend',b'custom')
    assert current_set(context)==original

@pytest.mark.parametrize('suffix',['-wal','-shm'])
def test_candidate_proof_binds_sidecars_as_well_as_main(candidate,suffix):
    context,service=candidate;residual_wal(context)
    service.probe(context,'friend',b'custom')
    path=Path(str(context.source)+suffix)
    data=bytearray(path.read_bytes());data[-1]^=1;path.write_bytes(data)
    before=current_set(context)
    with pytest.raises(OfflineError,match='日志.*变化'):
        service.apply(context,'friend',b'custom',_candidate_trial=True)
    assert current_set(context)==before


def test_recover_recognized_interruption_between_main_and_wal(fixture):
    context,service=fixture;original=residual_wal(context)
    bundle=Path(service.apply(context,'friend',b'custom'))
    # Simulate crash after new main was flushed but before WAL/SHM were reset.
    for suffix in ('-wal','-shm'): Path(str(context.source)+suffix).write_bytes(original[suffix])
    jp=bundle/'operation.json';record=json.loads(jp.read_text());record['state']='writing';jp.write_text(json.dumps(record))
    assert service.recover('account',context=context)==1
    assert current_set(context)==original

@pytest.mark.skipif(os.name!='nt',reason='Windows three-file exclusive handles integration')
def test_windows_exclusive_main_and_sidecar_writeback(fixture):
    context,service=fixture;residual_wal(context);service.lock=WindowsFile
    service.probe(context,'friend',b'windows-wal')
    service.apply(context,'friend',b'windows-wal')
    assert avatar(context)==b'windows-wal'
    assert avatar(context,'untouched')==b'wal-latest-other'

def test_valid_old_epoch_tail_is_not_replayed(fixture):
    import struct
    context,service=fixture;original=residual_wal(context)
    wal=bytearray(original['-wal']);frame=bytearray(wal[32:32+4120])
    frame[8:16]=bytes(value^0xff for value in wal[16:24])
    wal.extend(frame);Path(str(context.source)+'-wal').write_bytes(wal)
    before=current_set(context)
    report=service.probe(context,'friend',b'custom')
    assert report['stale_wal_tail']
    assert current_set(context)==before
    service.apply(context,'friend',b'custom')
    assert avatar(context,'untouched')==b'wal-latest-other'


def test_uncommitted_valid_frames_are_not_mistaken_for_commits(fixture):
    import struct
    from editor.wal import checksum,FRAME
    context,service=fixture;original=residual_wal(context)
    wal=bytearray(original['-wal']);magic=struct.unpack('>I',wal[:4])[0];endian='>' if magic&1 else '<'
    rolling=struct.unpack('>II',wal[24:32])
    for offset in range(32,len(wal),FRAME):
        wal[offset+4:offset+8]=b'\0'*4
        rolling=checksum(bytes(wal[offset:offset+8])+bytes(wal[offset+24:offset+FRAME]),rolling,endian)
        wal[offset+16:offset+24]=struct.pack('>II',*rolling)
    Path(str(context.source)+'-wal').write_bytes(wal)
    report=service.probe(context,'friend',b'custom')
    assert report['committed_wal_frames']==0 and report['uncommitted_wal_frames']>0
    service.apply(context,'friend',b'custom')
    # The other avatar update was never committed in this synthetic WAL.
    assert avatar(context,'untouched')==b'other'


def test_readonly_diagnostic_metadata_reflects_actual_encrypted_record(fixture):
    from editor.observe import read_details
    import hashlib
    context, service = fixture
    service.apply(context, 'friend', b'custom')
    before = context.source.read_bytes()
    observed = read_details(context, 'friend', _validate=lambda c:None)
    assert observed['image'] == b'custom'
    assert observed['md5'] == hashlib.md5(b'custom').hexdigest()
    assert isinstance(observed['update_time'], int)
    assert context.source.read_bytes() == before


def test_timeline_detects_replacement_in_real_encrypted_database_without_sampler_writes(fixture):
    from editor.trace import Timeline
    from editor.observe import read_details
    context, service = fixture
    service.apply(context, 'friend', b'custom')
    timeline = Timeline(dict(desired_avatar=b'custom', original_avatar=b'old', last_applied=True),
                        context.detected_version)
    timeline.add(0, read_details(context, 'friend', _validate=lambda c:None), False)
    # Simulate a later independent writer, not the sampling code.
    service.apply(context, 'friend', b'refreshed')
    before = context.source.read_bytes()
    timeline.add(2, read_details(context, 'friend', _validate=lambda c:None), True)
    assert context.source.read_bytes() == before
    report = timeline.report('test')
    assert report['replacement_after_custom_observed'] is True
    assert report['events'][-1]['md5_matches_image_bytes'] is True
    assert report['events'][-1]['image_changed_since_last_read'] is True
