"""Experimental desktop remark cache. Never calls the official remark UI/API."""
from contextlib import closing
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
    definitions = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
    return {'schema_sha256': hashlib.sha256(json.dumps(definitions, ensure_ascii=False).encode()).hexdigest()}


def protected_digest(db, friend):
    """Every non-target contact and every row of every other main DB table."""
    digest = hashlib.sha256()
    tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    for (table,) in tables:
        digest.update(table.encode('utf-8') + b'\x00')
        query = 'SELECT * FROM ' + quote(table)
        rows = db.execute(query + ' WHERE username IS NOT ?' if table == 'contact' else query,
                          (friend,) if table == 'contact' else ())
        hashes = []
        for row in rows:
            h = hashlib.sha256()
            for value in row:
                if value is None: kind, data = b'n', b''
                elif isinstance(value, bytes): kind, data = b'b', value
                elif isinstance(value, str): kind, data = b's', value.encode('utf-8')
                elif isinstance(value, int): kind, data = b'i', str(value).encode()
                elif isinstance(value, float): kind, data = b'f', struct.pack('<d', value)
                else: raise CompatibilityError('联系人数据库存在未适配的数据类型')
                h.update(kind + struct.pack('<Q', len(data)) + data)
            hashes.append(h.digest())
        for row_hash in sorted(hashes): digest.update(row_hash)
        digest.update(struct.pack('<Q', len(hashes)))
    return digest.hexdigest()


class OfflineRemark(OfflineAvatar):
    require_trial = True
    database_name = 'contact.db'
    write_strategy = STRATEGY

    def __init__(self, backup_root, **kwargs):
        kwargs.setdefault('_validate', validate_contact)
        super().__init__(backup_root, **kwargs)
        self._last_original = None

    @staticmethod
    def _matches(db, friend, data, invalidate):
        if invalidate: return False
        rows = db.execute('SELECT remark,nick_name,local_type FROM contact WHERE username=?', (friend,)).fetchall()
        return len(rows) == 1 and rows[0][0] == decode(data) and rows[0][2] == 1

    def _mutate(self, path, key, friend, data, invalidate):
        if invalidate: raise OfflineError('禁止删除联系人记录')
        name = decode(data)
        with closing(open_cipher(path, key)) as db:
            metadata = schema(db)
            before = protected_digest(db, friend)
            db.execute('PRAGMA foreign_keys=ON')
            with db:
                db.execute('BEGIN IMMEDIATE')
                cursor = db.execute('SELECT * FROM contact WHERE username=?', (friend,))
                rows = cursor.fetchall()
                columns = [c[0] for c in cursor.description]
                if len(rows) != 1: raise OfflineError('联系人缺失或不唯一')
                original = dict(zip(columns, rows[0]))
                if original['local_type'] != 1: raise OfflineError('电脑备注试验仅支持普通好友')
                if not isinstance(original['remark'], str) or not original['remark']:
                    raise OfflineError('此试验仅支持已设置备注的测试好友；不会创建缺失的联系人或备注')
                self._last_original = original['remark']
                expected = list(rows[0]); expected[columns.index('remark')] = name
                db.execute('UPDATE contact SET remark=? WHERE username=?', (name, friend))
            checkpoint_copy(db)
        verify_pages(path, key)
        with closing(open_cipher(path, key)) as db:
            if db.execute('SELECT * FROM contact WHERE username=?', (friend,)).fetchall() != [tuple(expected)]:
                raise OfflineError('电脑备注目标记录校验失败')
            if protected_digest(db, friend) != before: raise OfflineError('电脑备注试验影响了其他联系人或数据表')
            if schema(db) != metadata or db.execute('PRAGMA foreign_key_check').fetchall():
                raise OfflineError('联系人结构或关联校验失败')
            engine = db.execute('PRAGMA cipher_version').fetchone()[0]
        return metadata, engine

    def probe(self, context, friend, data, **kwargs):
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
        if _invalidate: raise OfflineError('禁止删除联系人记录')
        if not _candidate_trial: raise OfflineError('电脑备注仅允许完成副本检查后的单次测试，不开放普通写入')
        # Unlike ordinary avatar writes, every supported version requires a proof.
        self._check_proof(context, friend, data, False)
        return super().apply(context, friend, data, _candidate_trial=True, **kwargs)

    def invalidate(self, *args, **kwargs):
        raise OfflineError('禁止删除联系人记录；恢复电脑备注必须使用保存的原备注')
