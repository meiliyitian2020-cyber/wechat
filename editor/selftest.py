"""Frozen executable smoke test, using only newly created artificial data."""
from pathlib import Path
from contextlib import closing
import json
import os
import secrets
import tempfile
from .offline import Context, OfflineAvatar
from .cipher_stage import open_cipher
from .observe import read_current

def run(output):
    report={'schema':1,'ok':False,'stages':[]}
    code=1
    try:
        if os.name!='nt': raise RuntimeError('Windows executable self-test required')
        from sqlcipher3 import dbapi2 as sql
        report['stages'].append('engine_import')
        with tempfile.TemporaryDirectory(prefix='profile-exe-selftest-') as temp:
            root=Path(temp)/'fixture-account';source=root/'db_storage/head_image/head_image.db'
            source.parent.mkdir(parents=True);key=secrets.token_bytes(32)
            with closing(sql.connect(source)) as db:
                db.execute('PRAGMA key = "x\''+key.hex()+'\'"')
                report['cipher_version']=db.execute('PRAGMA cipher_version').fetchone()[0]
                db.execute('CREATE TABLE head_image(username TEXT PRIMARY KEY,image_buffer BLOB,md5 TEXT,update_time INTEGER)')
                db.execute('INSERT INTO head_image VALUES(?,?,?,?)',('artificial-friend',b'original','synthetic-remote-avatar-token',1700000000));db.commit()
            # Reproduce residual WAL/SHM after exit, including an uncheckpointed commit.
            db=open_cipher(source,key)
            try:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('PRAGMA wal_autocheckpoint=0')
                db.execute('UPDATE head_image SET image_buffer=? WHERE username=?',(b'committed-in-wal','artificial-friend'))
                db.commit()
                snapshot={suffix:Path(str(source)+suffix).read_bytes() for suffix in ('','-wal','-shm')}
            finally:
                db.close()
            for suffix,data in snapshot.items(): Path(str(source)+suffix).write_bytes(data)
            context=Context(source,root,root.name,Path(temp)/'nonexistent-test-executable',key)
            # These bypasses apply only to this function's freshly generated fixture.
            # The real Windows exclusive file handle is deliberately NOT bypassed.
            service=OfflineAvatar(Path(temp)/'backups',_guard=lambda:None,
                                  _validate=lambda c:None,_version=lambda c:None,_read_version=lambda c:None)
            original=source.read_bytes()
            initial_probe=service.probe(context,'artificial-friend',b'custom')
            if initial_probe['committed_wal_frames']<1: raise RuntimeError('WAL recovery not exercised')
            for suffix,data in snapshot.items():
                if Path(str(source)+suffix).read_bytes()!=data: raise RuntimeError('probe modified source set')
            report['stages'].append('residual_wal_and_shm_copy_recovery')
            bundle=Path(service.apply(context,'artificial-friend',b'custom'))
            report['stages'].append('exclusive_writeback')
            if (bundle/'original.enc').read_bytes()!=original: raise RuntimeError('backup mismatch')
            if (bundle/'wal.enc').read_bytes()!=snapshot['-wal']: raise RuntimeError('WAL backup mismatch')
            if (bundle/'shm.bin').read_bytes()!=snapshot['-shm']: raise RuntimeError('SHM backup mismatch')
            seen=read_current(context,['artificial-friend'],_validate=lambda c:None)
            if seen['artificial-friend']!=b'custom': raise RuntimeError('readback mismatch')
            with closing(open_cipher(source,key)) as check:
                fields=check.execute('SELECT md5,update_time FROM head_image').fetchone()
                if fields!=('synthetic-remote-avatar-token',1700000000):
                    raise RuntimeError('avatar metadata not preserved')
            report['stages'].append('encrypted_observation_and_preserved_non_image_fields')
            context.detected_version='4.1.15.56'
            snapshot=source.read_bytes()
            probe=service.probe(context,'artificial-friend',b'probe-only-avatar')
            if not probe['roundtrip_ok'] or source.read_bytes()!=snapshot:
                raise RuntimeError('candidate probe mismatch')
            report['stages'].append('candidate_copy_compatibility_probe')
            service.apply(context,'artificial-friend',b'probe-only-avatar',_candidate_trial=True)
            seen=read_current(context,['artificial-friend'],_validate=lambda c:None)
            if seen['artificial-friend']!=b'probe-only-avatar': raise RuntimeError('candidate trial mismatch')
            report['stages'].append('candidate_gated_trial_write')
            service.probe(context,'artificial-friend',None,_invalidate=True)
            service.invalidate(context,'artificial-friend',_candidate_trial=True)
            with closing(open_cipher(source,key)) as db:
                if db.execute('SELECT count(*) FROM head_image').fetchone()[0]!=0:
                    raise RuntimeError('invalidation mismatch')
            report['stages'].append('invalidation')
            # Exercise the bundled Qt platform plugins without discovering real accounts.
            os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
            from PySide6.QtWidgets import QApplication
            from .core import Store
            from . import gui
            def synthetic_discover(): raise RuntimeError('人工数据打包自检')
            gui.discover=synthetic_discover
            app=QApplication.instance() or QApplication([])
            window=gui.Window(Store(Path(temp)/'gui-state'))
            window.show();app.processEvents()
            if not window.name.isEnabled() or '待验收' not in window.name.placeholderText():
                raise RuntimeError('experimental remark editor missing scope warning')
            if not window.close(): raise RuntimeError('window did not close')
            app.processEvents()
            report['stages'].append('packaged_qt_gui_startup_and_shutdown')
            from .trace import Timeline, LoginTrace
            from .observe import read_details
            baseline=source.read_bytes()
            timeline=Timeline(dict(desired_avatar=b'custom',original_avatar=b'original',last_applied=True),
                              context.detected_version)
            sampler=LoginTrace(context,'artificial-friend',timeline,
                               reader=lambda c,f:read_details(c,f,_validate=lambda c:None),
                               process=lambda:False,interval=.01,duration=.5)
            sampler.start()
            if not sampler.wait(5000) or sampler.isRunning():
                raise RuntimeError('diagnostic thread did not stop')
            if sampler.result is None or sampler.result['samples'] < 1 or source.read_bytes()!=baseline:
                raise RuntimeError('diagnostic sampler failed')
            report['stages'].append('packaged_bounded_readonly_diagnostic_thread')
            # A separate synthetic contact DB: original nickname CCCCC, remark A -> B.
            from .local_remark import OfflineRemark, payload
            from .observe import read_remark
            remark_source=root/'db_storage/contact/contact.db'
            remark_source.parent.mkdir(parents=True)
            remark_key=secrets.token_bytes(32)
            with closing(sql.connect(remark_source)) as db:
                db.execute('PRAGMA key = "x\''+remark_key.hex()+'\'"')
                db.execute('CREATE TABLE contact(username TEXT PRIMARY KEY,nick_name TEXT,remark TEXT,local_type INTEGER,flag INTEGER)')
                db.execute('INSERT INTO contact VALUES(?,?,?,?,?)',('synthetic-contact','CCCCC','A',1,917))
                db.execute('CREATE TABLE synthetic_sync_queue(id INTEGER,payload BLOB)')
                db.execute('INSERT INTO synthetic_sync_queue VALUES(1,?)',(b'unchanged',))
                db.commit()
            remark_context=Context(remark_source,root,root.name,context.executable,remark_key,'4.1.15.56')
            remark_service=OfflineRemark(Path(temp)/'remark-backups',_guard=lambda:None,
                                         _validate=lambda c:None,_version=lambda c:None,_read_version=lambda c:None)
            original=remark_source.read_bytes()
            probe=remark_service.probe(remark_context,'synthetic-contact',payload('B'))
            if remark_source.read_bytes()!=original or not probe['target_non_remark_fields_unchanged']:
                raise RuntimeError('remark copy probe failed')
            remark_service.apply(remark_context,'synthetic-contact',payload('B'),_candidate_trial=True)
            current=read_remark(remark_context,'synthetic-contact',_validate=lambda c:None)
            if current['name']!='B' or current['nickname']!='CCCCC':
                raise RuntimeError('remark or original nickname mismatch')
            with closing(open_cipher(remark_source,remark_key)) as db:
                if db.execute('SELECT flag FROM contact').fetchone()[0]!=917:
                    raise RuntimeError('remark trial modified flags')
                if db.execute('SELECT * FROM synthetic_sync_queue').fetchall()!=[(1,b'unchanged')]:
                    raise RuntimeError('remark trial modified other tables')
            remark_service.probe(remark_context,'synthetic-contact',payload('A'))
            remark_service.apply(remark_context,'synthetic-contact',payload('A'),_candidate_trial=True)
            if read_remark(remark_context,'synthetic-contact',_validate=lambda c:None)['name']!='A':
                raise RuntimeError('remark restore mismatch')
            report['stages'].append('packaged_synthetic_desktop_remark_A_B_A_and_protected_fields')
            from .errors import remark_failure
            failure=remark_failure(RuntimeError('synthetic-private-message'),
                                   version='4.1.15.56',gui_stage='save_write_history',service=remark_service)
            if failure['source_unchanged_by_this_operation'] or 'synthetic-private-message' in json.dumps(failure):
                raise RuntimeError('post-write failure scope or redaction mismatch')
            original=remark_source.read_bytes()
            def stopped_check_failed(): raise RuntimeError('synthetic-private-process-error')
            remark_service.guard=stopped_check_failed
            try:
                remark_service.probe(remark_context,'synthetic-contact',payload('B'))
                raise AssertionError('failed process check was ignored')
            except RuntimeError as exc:
                failure=remark_failure(exc,version='4.1.15.56',gui_stage='copy_probe',service=remark_service)
            if failure['source_write_started'] or not failure['source_unchanged_by_this_operation']:
                raise RuntimeError('pre-write failure scope mismatch')
            if remark_source.read_bytes()!=original or failure['backend_stage']!='probe_process_check':
                raise RuntimeError('failure stage or source snapshot mismatch')
            report['stages'].append('packaged_redacted_error_reports_and_precise_source_outcomes')
        report['ok']=True;code=0
    except Exception as exc:
        report['error_type']=type(exc).__name__
    with Path(output).open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    return code
