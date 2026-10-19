"""Experimental offline avatar writeback. No contact/name writes, no hooks.

Writes only head_image.db while holding a Windows exclusive handle. WAL/SHM
presence is rejected. A durable encrypted backup and journal precede mutation.
This does not prove which cache the WeChat client renders.
"""
from __future__ import annotations
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
import ctypes
import hashlib
import json
import os
import tempfile
import uuid
import time
from .cipher_stage import open_cipher, verify_pages, CipherError
from .core import now
from .windows import ensure_wechat_stopped, version, TARGET, check_read_version, check_write_version, WRITE_VERSIONS
from .compatibility import inspect_head, other_rows_digest, CompatibilityError

class OfflineError(RuntimeError):
    pass

@dataclass(repr=False)
class Context:
    source: Path
    account_root: Path
    account: str
    executable: Path
    key: bytes = field(repr=False)
    detected_version: str = TARGET

class WindowsFile:
    """Deny all sharing, including deletion, until rollback/commit completes."""
    def __init__(self,path):
        if os.name != 'nt': raise OfflineError('真实写回仅支持 Windows')
        from ctypes import wintypes as w
        self.api = ctypes.WinDLL('kernel32',use_last_error=True)
        self.api.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,w.LPVOID,w.DWORD,w.DWORD,w.HANDLE]
        self.api.CreateFileW.restype=w.HANDLE
        self.api.CloseHandle.argtypes=[w.HANDLE]; self.api.CloseHandle.restype=w.BOOL
        self.handle=self.api.CreateFileW(str(path),0xC0000000,0,None,3,0x80000000,None)
        if self.handle == ctypes.c_void_p(-1).value:
            raise OfflineError('无法独占头像库；请完全退出微信，并确认文件权限')
        # Transfer HANDLE ownership to a Python file descriptor. No second open.
        import msvcrt
        try:
            fd=msvcrt.open_osfhandle(self.handle,os.O_RDWR|os.O_BINARY)
        except BaseException:
            self.api.CloseHandle(self.handle); raise
        try:
            self.file=os.fdopen(fd,'r+b',buffering=0)
        except BaseException:
            os.close(fd);raise
    def __enter__(self): return self
    def __exit__(self,*args): self.file.close()
    def read(self):
        self.file.seek(0)
        if os.fstat(self.file.fileno()).st_size>512*1024*1024:
            raise OfflineError('头像库超过当前安全处理大小上限')
        return self.file.read()
    def write(self,data):
        # Write changed pages only; unrelated encrypted pages remain byte-identical.
        old=self.read()
        for offset in range(0,len(data),4096):
            chunk=data[offset:offset+4096]
            if chunk!=old[offset:offset+4096]:
                self.file.seek(offset)
                view=memoryview(chunk)
                while view:
                    n=self.file.write(view)
                    if not n: raise OfflineError('头像库写入中断')
                    view=view[n:]
        self.file.truncate(len(data)); self.file.flush(); os.fsync(self.file.fileno())

def hash_bytes(data): return hashlib.sha256(data).hexdigest()

def durable(path,data):
    with path.open('xb') as out:
        out.write(data);out.flush();os.fsync(out.fileno())

def journal(path,data):
    temp=path.with_suffix('.new')
    with temp.open('w',encoding='utf-8') as out:
        json.dump(data,out,ensure_ascii=False);out.flush();os.fsync(out.fileno())
    os.replace(temp,path)

def validate_context(context):
    source=context.source.absolute()
    root=context.account_root.absolute()
    if not source.is_relative_to(root) or source.resolve(strict=True)!=source or root.resolve(strict=True)!=root:
        raise OfflineError('账号路径必须为真实目录')
    if source.name!='head_image.db' or not source.resolve(strict=True).is_relative_to(root):
        raise OfflineError('头像库不属于选定账号')
    if root.name!=context.account:
        raise OfflineError('账号目录不匹配')
    current=source
    while current!=root.parent:
        if current.is_symlink() or getattr(current,'is_junction',lambda:False)():
            raise OfflineError('拒绝符号链接或目录联接中的头像库')
        current=current.parent
    if len(context.key)!=32:
        raise OfflineError('当前数据库密钥格式未适配写入')
    validate_read_executable(context)

def validate_read_executable(context):
    try:
        check_read_version(version(context.executable),context.detected_version)
    except RuntimeError as exc:
        raise OfflineError(str(exc)) from None

def validate_executable(context):
    try:
        check_write_version(version(context.executable),context.detected_version)
    except RuntimeError as exc:
        raise OfflineError(str(exc)) from None

def no_sidecars(source):
    if any(Path(str(source)+suffix).exists() for suffix in ('-wal','-shm')):
        raise OfflineError('头像库仍有 WAL/SHM。请正常退出微信后重试；不要手动删除这些文件。')

class OfflineAvatar:
    def __init__(self,backup_root,*,_lock=WindowsFile,_guard=ensure_wechat_stopped,_validate=validate_context,_version=validate_executable,_read_version=validate_read_executable):
        self.backup_root=Path(backup_root)
        self.lock,self.guard,self.validate,self.check_version=_lock,_guard,_validate,_version
        self.check_read_version=_read_version
        self._probes={}

    def apply(self,context,friend,avatar,*,_fault=None,_invalidate=False,_candidate_trial=False):
        if not friend or (not avatar and not _invalidate): raise OfflineError('未选择好友或自定义头像')
        self.validate(context)
        trial=_candidate_trial and context.detected_version not in WRITE_VERSIONS
        proof_key=(id(context),friend,bool(_invalidate))
        proof=None
        if trial:
            self.check_read_version(context)
            proof=self._probes.get(proof_key)
            if (not proof or proof['version']!=context.detected_version
                    or proof['source_path']!=str(context.source.absolute()) or proof['account']!=context.account
                    or proof['avatar_hash']!=hash_bytes(avatar or b'') or time.monotonic()-proof['time']>300):
                raise OfflineError('需要先对当前好友和图片完成本次副本检查；检查凭据已缺失或过期')
        else:
            self.check_version(context)
        self.guard();no_sidecars(context.source)
        source_path=context.source.resolve(strict=True)
        root=self.backup_root.resolve()
        if root.is_relative_to(context.account_root.resolve()):
            raise OfflineError('备份目录必须位于微信账号目录之外')
        root.mkdir(parents=True,exist_ok=True)
        # Unresolved prior operations block subsequent writes to this account.
        for path in root.glob('*/operation.json'):
            record=json.loads(path.read_text(encoding='utf-8'))
            if record.get('account')==context.account and record.get('state') in {'prepared','writing','recovery_required'}:
                raise OfflineError('此账号存在未完成操作，请先执行故障恢复')
        with self.lock(context.source) as locked:
            self.guard();no_sidecars(context.source)
            original=locked.read()
            if trial and hash_bytes(original)!=proof['source_hash']:
                raise OfflineError('原始头像库在副本检查后发生变化，请重新检查，禁止沿用旧检查结果')
            with tempfile.TemporaryDirectory(prefix='avatar-transaction-') as temp:
                stage=Path(temp)/'head_image.db';stage.write_bytes(original)
                verify_pages(stage,context.key)
                with closing(open_cipher(stage,context.key)) as db:
                    inspect_head(db)
                    other_before=other_rows_digest(db,friend)
                    db.execute('PRAGMA foreign_keys=ON')
                    with db:
                        db.execute('BEGIN IMMEDIATE')
                        rows=db.execute('SELECT image_buffer FROM head_image WHERE username=?',(friend,)).fetchall()
                        if len(rows)!=1: raise OfflineError('目标好友头像记录缺失或不唯一')
                        if _invalidate:
                            db.execute('DELETE FROM head_image WHERE username=?',(friend,))
                        else:
                            db.execute('UPDATE head_image SET image_buffer=?,md5=?,update_time=? WHERE username=?',
                                       (avatar,hashlib.md5(avatar).hexdigest(),int(__import__('time').time()),friend))
                verify_pages(stage,context.key)
                with closing(open_cipher(stage,context.key)) as db:
                    if other_rows_digest(db,friend)!=other_before:
                        raise OfflineError('暂存修改影响了其他好友，已拒绝写回')
                    if not self._matches(db,friend,avatar,_invalidate):
                        raise OfflineError('目标头像校验失败')
                replacement=stage.read_bytes()
                self.guard()
                if trial: self.check_read_version(context)
                else: self.check_version(context)
                no_sidecars(context.source)
                if trial:
                    if time.monotonic()-proof['time']>300 or self._probes.get(proof_key) is not proof:
                        raise OfflineError('副本检查凭据已过期或被使用，请重新检查')
                    self._probes.pop(proof_key)
                if locked.read()!=original: raise OfflineError('头像库已发生变化，禁止继续')
                bundle=root/uuid.uuid4().hex;bundle.mkdir()
                durable(bundle/'original.enc',original)
                record={'schema':1,'account':context.account,'friend':friend,'action':'invalidate' if _invalidate else 'apply','source':str(source_path),
                        'wechat_version':context.detected_version,'candidate_trial':bool(trial),
                        'state':'prepared','created':now(),'original_hash':hash_bytes(original),
                        'replacement_hash':hash_bytes(replacement),'wal_present':False,'shm_present':False}
                jp=bundle/'operation.json';journal(jp,record)
                try:
                    record['state']='writing';journal(jp,record)
                    if _fault: _fault('before_write')
                    locked.write(replacement)
                    if _fault: _fault('after_write')
                    if locked.read()!=replacement: raise OfflineError('写入后文件校验失败')
                    # Reopen a copy of the actual bytes read from the exclusive handle.
                    stage.write_bytes(locked.read());verify_pages(stage,context.key)
                    with closing(open_cipher(stage,context.key)) as db:
                        if not self._matches(db,friend,avatar,_invalidate):
                            raise OfflineError('实际写入记录验证失败')
                    record['state']='committed';record['completed']=now();journal(jp,record)
                except BaseException:
                    try:
                        locked.write(original)
                        if locked.read()!=original: raise OfflineError('回滚文件校验失败')
                        record['state']='rolled_back';journal(jp,record)
                    except BaseException:
                        record['state']='recovery_required';journal(jp,record)
                        raise OfflineError('回滚未完成，请保持微信关闭，保留工具备份并执行故障恢复') from None
                    raise OfflineError('写入失败，已恢复操作前的加密头像库') from None
        return str(bundle)

    def probe(self,context,friend,avatar,*,_invalidate=False):
        """Offline dry-run on a private encrypted copy. Original is never written."""
        if not friend or (not avatar and not _invalidate): raise OfflineError('请先选择测试好友和自定义头像')
        self.validate(context);self.guard();no_sidecars(context.source)
        with self.lock(context.source) as locked:
            self.guard();no_sidecars(context.source)
            original=locked.read()
            with tempfile.TemporaryDirectory(prefix='avatar-compatibility-') as temp:
                path=Path(temp)/'head_image.db';path.write_bytes(original)
                verify_pages(path,context.key)
                with closing(open_cipher(path,context.key)) as db:
                    metadata=inspect_head(db)
                    before=other_rows_digest(db,friend)
                    db.execute('PRAGMA foreign_keys=ON')
                    with db:
                        db.execute('BEGIN IMMEDIATE')
                        if db.execute('SELECT count(*) FROM head_image WHERE username=?',(friend,)).fetchone()[0]!=1:
                            raise OfflineError('测试好友头像记录缺失或不唯一')
                        if _invalidate:
                            db.execute('DELETE FROM head_image WHERE username=?',(friend,))
                        else:
                            db.execute('UPDATE head_image SET image_buffer=?,md5=?,update_time=? WHERE username=?',
                                       (avatar,hashlib.md5(avatar).hexdigest(),int(time.time()),friend))
                verify_pages(path,context.key)
                with closing(open_cipher(path,context.key)) as db:
                    if not self._matches(db,friend,avatar,_invalidate): raise OfflineError('副本目标记录验证失败')
                    if other_rows_digest(db,friend)!=before: raise OfflineError('副本修改影响了其他好友')
                    if db.execute('PRAGMA foreign_key_check').fetchall(): raise OfflineError('副本关联完整性检查失败')
                    engine=db.execute('PRAGMA cipher_version').fetchone()[0]
                    if inspect_head(db)!=metadata: raise OfflineError('副本表结构发生变化')
                self.guard();self.check_read_version(context);no_sidecars(context.source)
                if locked.read()!=original: raise OfflineError('源文件发生变化，检查结果无效')
                self._probes[(id(context),friend,bool(_invalidate))]={
                    'source_path':str(context.source.absolute()),'account':context.account,
                    'source_hash':hash_bytes(original),'avatar_hash':hash_bytes(avatar or b''),
                    'version':context.detected_version,'time':time.monotonic()}
                return {'report_version':1,'wechat_version':context.detected_version,
                        'operation':'invalidate' if _invalidate else 'apply',
                        'cipher_version':engine,'page_size':4096,'reserve_bytes':80,
                        'source_unchanged':True,'all_page_hmac_ok':True,'roundtrip_ok':True,
                        'non_target_rows_unchanged':True,'original_database_written':False,
                        'client_rendering_verified':False,**metadata}

    @staticmethod
    def _matches(db,friend,avatar,invalidate):
        rows=db.execute('SELECT image_buffer FROM head_image WHERE username=?',(friend,)).fetchall()
        return not rows if invalidate else len(rows)==1 and rows[0][0]==avatar

    def invalidate(self,context,friend,*,_candidate_trial=False):
        # Request WeChat to fetch an avatar, rather than reinstating a stale backup.
        return self.apply(context,friend,None,_invalidate=True,_candidate_trial=_candidate_trial)

    def recover(self,account,*,context=None):
        """Recover interrupted operations only when no third-party changes exist."""
        if context is not None:
            self.validate(context)
            if context.account!=account: raise OfflineError('恢复账号不匹配')
        elif self.lock is WindowsFile:
            raise OfflineError('恢复前需要读取账号并确认微信版本')
        count=0
        for jp in self.backup_root.glob('*/operation.json'):
            record=json.loads(jp.read_text(encoding='utf-8'))
            if record.get('account')!=account or record.get('state') not in {'prepared','writing','recovery_required'}: continue
            if context is not None and record.get('wechat_version',TARGET)!=context.detected_version:
                raise OfflineError('恢复记录与当前微信版本不匹配，禁止跨版本恢复')
            source=Path(record['source'])
            if context is not None and source!=context.source.resolve(): raise OfflineError('恢复来源与当前账号不匹配')
            original=(jp.parent/'original.enc').read_bytes()
            if source.name!='head_image.db' or hash_bytes(original)!=record['original_hash']:
                raise OfflineError('恢复备份校验失败')
            self.guard();no_sidecars(source)
            with self.lock(source) as locked:
                self.guard();no_sidecars(source)
                if context is not None: self.check_read_version(context)
                current=hash_bytes(locked.read())
                if current not in {record['original_hash'],record['replacement_hash']}:
                    raise OfflineError('检测到部分写入或微信后续变化，无法自动区分。已停止恢复以保护现有数据。')
                locked.write(original)
                if hash_bytes(locked.read())!=record['original_hash']: raise OfflineError('恢复验证失败')
                record['state']='rolled_back';journal(jp,record);count+=1
        return count
