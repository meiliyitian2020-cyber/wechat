"""Read a stable private copy of the live encrypted main DB and WAL.

Unlike the upstream cached reader, this never falls back to a stale snapshot.
No data or keys are sent to any server.
"""
from pathlib import Path
from contextlib import closing
import tempfile
from .cipher_stage import open_cipher, verify_pages
from .offline import OfflineError, validate_context
from .wal import validate_wal

def read_current(context, friends, *, _validate=validate_context, _details=False):
    _validate(context)
    source=context.source
    for attempt in range(3):
        with tempfile.TemporaryDirectory(prefix='avatar-observe-') as temp:
            try:
                files={}
                for suffix in ('','-wal'):
                    p=Path(str(source)+suffix)
                    if p.exists():
                        if p.is_symlink() or p.stat().st_size>512*1024*1024:
                            raise OfflineError('头像文件不符合安全读取条件')
                        files[suffix]=p.read_bytes()
                if '' not in files: raise OfflineError('头像数据库不存在')
                for suffix,data in files.items(): (Path(temp)/('head_image.db'+suffix)).write_bytes(data)
                # Presence and byte equality must remain stable throughout copy.
                stable=True
                for suffix in ('','-wal'):
                    p=Path(str(source)+suffix)
                    if p.exists()!=(suffix in files) or (suffix in files and p.read_bytes()!=files[suffix]):
                        stable=False;break
                if not stable: continue
                path=Path(temp)/'head_image.db'
                verify_pages(path,context.key)
                validate_wal(files[''],files.get('-wal'),context.key)
                with closing(open_cipher(path,context.key)) as db:
                    if db.execute('PRAGMA cipher_integrity_check').fetchall():
                        raise OfflineError('加密数据库完整性校验失败')
                    result={}
                    for friend in friends:
                        columns='image_buffer,md5,update_time' if _details else 'image_buffer'
                        rows=db.execute('SELECT '+columns+' FROM head_image WHERE username=?',(friend,)).fetchall()
                        if len(rows)>1: raise OfflineError('头像记录不唯一')
                        # Empty bytes means an observed missing record, distinct from read failure.
                        image=bytes(rows[0][0]) if rows and rows[0][0] else b''
                        result[friend]=({'image':image, 'md5':rows[0][1] if rows else None,
                                         'update_time':rows[0][2] if rows else None} if _details else image)
                return result
            except (FileNotFoundError,PermissionError):
                continue
    raise OfflineError('微信头像库正在变化或无法读取，当前无法验证')


def read_details(context, friend, *, _validate=validate_context):
    return read_current(context, [friend], _validate=_validate, _details=True)[friend]
