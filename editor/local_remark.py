"""Experimental desktop remark cache. Never calls the official remark UI/API."""
from contextlib import closing, contextmanager
import hashlib
import json
import struct
from .offline import OfflineAvatar, OfflineError, validate_context, checkpoint_copy
from .cipher_stage import open_cipher, verify_pages
from .compatibility import CompatibilityError

STRATEGY = 'contact_remark_only_preserve_other_fields'


def payload(name, *, restore=False):
    if not isinstance(name, str) or len(name) > 64 or (not name and not restore):
        raise OfflineError('电脑备注请输入 1 至 64 个字符')
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise OfflineError('电脑备注不能包含换行、制表或控制字符')
    return json.dumps({'remark': name}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def decode(data):
    try:
        value = json.loads(data.decode('utf-8'))
        if not isinstance(value, dict) or set(value) != {'remark'}:
            raise ValueError()
        payload(value['remark'], restore=True)
        return value['remark']
    except Exception:
        raise OfflineError('电脑备注配置格式无效') from None


def validate_contact(context):
    if context.detected_version != '4.1.15.56':
        raise OfflineError('电脑备注缓存试验目前仅开放 4.1.15.56')
    validate_context(context, _expected_database='contact.db')


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def schema(db):
    fields = db.execute('PRAGMA table_info(contact)').fetchall()
    if not {'username', 'nick_name', 'remark', 'local_type'} <= {r[1] for r in fields}:
        raise CompatibilityError('联系人结构缺少电脑备注试验所需字段')
    kinds = db.execute("SELECT type FROM pragma_table_list WHERE schema='main' AND name='contact' COLLATE NOCASE").fetchall()
    if kinds != [('table',)]:
        raise CompatibilityError('联系人不是已适配的普通数据表')
    if db.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND tbl_name='contact' COLLATE NOCASE").fetchone()[0]:
        raise CompatibilityError('联系人表有未适配触发器，禁止电脑备注试写')
    if db.execute('PRAGMA encoding').fetchone()[0].upper().replace('-','')!='UTF8':
        raise CompatibilityError('联系人文本编码尚未适配，禁止写入')
    definitions = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
    return {'schema_sha256': hashlib.sha256(json.dumps(definitions, ensure_ascii=False).encode()).hexdigest()}


@contextmanager
def raw_rows(db, table, suffix='', parameters=()):
    """Keep SQLite TEXT/BLOB types distinct without decoding stored TEXT bytes."""
    previous=db.text_factory;cursor=None
    try:
        db.text_factory=str
        fields=db.execute('PRAGMA table_xinfo('+quote(table)+')').fetchall()
        columns=[f[1] for f in fields if f[6]!=1]  # SELECT * excludes hidden virtual columns.
        if not columns: raise CompatibilityError('无法确定数据表字段，禁止写入')
        expressions=[]
        for name in columns: expressions.extend(('typeof('+quote(name)+')',quote(name)))
        db.text_factory=bytes
        cursor=db.execute('SELECT '+','.join(expressions)+' FROM '+quote(table)+suffix,parameters)
        yield columns,cursor
    finally:
        db.text_factory=previous
        if cursor is not None: cursor.close()


def raw_target(db, friend):
    with raw_rows(db,'contact',' WHERE username=? LIMIT 2',(friend,)) as (columns,rows):
        return columns,[tuple(row) for row in rows]


def protected_digest(db, friend, progress=None):
    """Every non-target contact and every row of every other main DB table."""
    digest=hashlib.sha256()
    if progress:progress({'query_stage':'table_list'})
    tables=db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    for ordinal,(table,definition) in enumerate(tables,1):
        state={'table_ordinal':ordinal,'table_is_contact':table=='contact',
               'table_is_virtual':bool(definition and definition.lstrip().upper().startswith('CREATE VIRTUAL TABLE')),
               'query_stage':'table_metadata'}
        if progress:progress(state)
        digest.update(table.encode('utf-8')+b'\x00')
        suffix=' WHERE username IS NOT ?' if table=='contact' else ''
        parameters=(friend,) if table=='contact' else ()
        hashes=[]
        with raw_rows(db,table,suffix,parameters) as (columns,rows):
            state=dict(state,query_stage='row_read',column_count=len(columns))
            if progress:progress(state)
            for row in rows:
                h=hashlib.sha256()
                for offset in range(0,len(row),2):
                    kind,value=row[offset:offset+2]
                    if kind==b'null':data=b''
                    elif kind in (b'text',b'blob') and isinstance(value,bytes):data=value
                    elif kind==b'integer' and isinstance(value,int):data=str(value).encode('ascii')
                    elif kind==b'real' and isinstance(value,float):data=struct.pack('<d',value)
                    else:raise CompatibilityError('联系人数据库存在未适配的数据类型')
                    h.update(kind+b'\x00'+struct.pack('<Q',len(data))+data)
                hashes.append(h.digest())
        for row_hash in sorted(hashes):digest.update(row_hash)
        digest.update(struct.pack('<Q',len(hashes)))
    if progress:progress(None)
    return digest.hexdigest()


class OfflineRemark(OfflineAvatar):
    require_trial = True
    database_name = 'contact.db'
    write_strategy = STRATEGY

    def __init__(self, backup_root, **kwargs):
        kwargs.setdefault('_validate', validate_contact)
        super().__init__(backup_root, **kwargs)
        self._last_original = None
        self.digest_context = None

    @staticmethod
    def _matches(db, friend, data, invalidate):
        if invalidate: return False
        rows = db.execute('SELECT remark,local_type FROM contact WHERE username=?', (friend,)).fetchall()
        return len(rows) == 1 and rows[0][0] == decode(data) and rows[0][1] == 1

    def digest_progress(self, state):
        self.digest_context=state

    def _mutate(self, path, key, friend, data, invalidate):
        if invalidate: raise OfflineError('禁止删除联系人记录')
        name = decode(data)
        with closing(open_cipher(path, key)) as db:
            self.phase('remark_schema')
            metadata = schema(db)
            self.phase('remark_other_tables_digest_before')
            before = protected_digest(db, friend, self.digest_progress)
            db.execute('PRAGMA foreign_keys=ON')
            self.phase('remark_target_update_in_copy')
            with db:
                db.execute('BEGIN IMMEDIATE')
                columns,rows=raw_target(db,friend)
                if len(rows)!=1:raise OfflineError('联系人缺失或不唯一')
                original={column:tuple(rows[0][i*2:i*2+2]) for i,column in enumerate(columns)}
                if original['local_type']!=(b'integer',1):raise OfflineError('电脑备注试验仅支持普通好友')
                kind,remark=original['remark']
                if kind!=b'text' or not isinstance(remark,bytes) or not remark:
                    raise OfflineError('此试验仅支持已设置文字备注的测试好友')
                self._last_original=remark.decode('utf-8')
                expected=list(rows[0]);offset=columns.index('remark')*2
                expected[offset:offset+2]=[b'text',name.encode('utf-8')]
                db.execute('UPDATE contact SET remark=? WHERE username=?', (name, friend))
            self.phase('remark_checkpoint_copy')
            checkpoint_copy(db)
        self.phase('remark_page_hmac')
        verify_pages(path, key)
        with closing(open_cipher(path, key)) as db:
            self.phase('remark_target_readback')
            after_columns,after=raw_target(db,friend)
            if after_columns!=columns or after!=[tuple(expected)]:
                raise OfflineError('电脑备注目标记录校验失败')
            self.phase('remark_other_tables_digest_after')
            if protected_digest(db, friend, self.digest_progress) != before: raise OfflineError('电脑备注试验影响了其他联系人或数据表')
            self.phase('remark_foreign_key_check')
            if schema(db) != metadata or db.execute('PRAGMA foreign_key_check').fetchall():
                raise OfflineError('联系人结构或关联校验失败')
            engine = db.execute('PRAGMA cipher_version').fetchone()[0]
        return metadata, engine

    def probe(self, context, friend, data, **kwargs):
        self.digest_context=None
        report = super().probe(context, friend, data, **kwargs)
        self._probes[(id(context),friend,False)]['original_remark'] = self._last_original
        self._last_original = None
        report.pop('target_non_image_fields_unchanged', None)
        report.update(operation='desktop_remark_copy_probe', target_non_remark_fields_unchanged=True,
                      remote_non_sync_verified=False, client_rendering_verified=False)
        return report

    def original_name(self, context, friend, data):
        _,proof=self._check_proof(context, friend, data, False)
        return proof['original_remark']

    def apply(self, context, friend, data, *, _candidate_trial=False, _invalidate=False, **kwargs):
        self.digest_context=None
        if _invalidate: raise OfflineError('禁止删除联系人记录')
        if not _candidate_trial: raise OfflineError('电脑备注仅允许完成副本检查后的单次测试，不开放普通写入')
        # Unlike ordinary avatar writes, every supported version requires a proof.
        self._check_proof(context, friend, data, False)
        return super().apply(context, friend, data, _candidate_trial=True, **kwargs)

    def invalidate(self, *args, **kwargs):
        raise OfflineError('禁止删除联系人记录；恢复电脑备注必须使用保存的原备注')
