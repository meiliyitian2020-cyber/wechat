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
                db.execute('INSERT INTO head_image VALUES(?,?,?,?)',('artificial-friend',b'original','',0));db.commit()
            context=Context(source,root,root.name,Path(temp)/'nonexistent-test-executable',key)
            # These bypasses apply only to this function's freshly generated fixture.
            # The real Windows exclusive file handle is deliberately NOT bypassed.
            service=OfflineAvatar(Path(temp)/'backups',_guard=lambda:None,
                                  _validate=lambda c:None,_version=lambda c:None,_read_version=lambda c:None)
            original=source.read_bytes()
            bundle=Path(service.apply(context,'artificial-friend',b'custom'))
            report['stages'].append('exclusive_writeback')
            if (bundle/'original.enc').read_bytes()!=original: raise RuntimeError('backup mismatch')
            seen=read_current(context,['artificial-friend'],_validate=lambda c:None)
            if seen['artificial-friend']!=b'custom': raise RuntimeError('readback mismatch')
            report['stages'].append('encrypted_observation')
            context.detected_version='4.1.15.65'
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
        report['ok']=True;code=0
    except Exception as exc:
        report['error_type']=type(exc).__name__
    with Path(output).open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    return code
