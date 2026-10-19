"""Conservative schema checks and a digest of every non-target avatar row."""
import hashlib
import json
import struct

class CompatibilityError(RuntimeError):
    pass

def inspect_head(db):
    columns=db.execute('PRAGMA table_info(head_image)').fetchall()
    if not {'username','image_buffer','md5','update_time'} <= {r[1] for r in columns}:
        raise CompatibilityError('头像表结构未适配')
    if db.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND tbl_name='head_image'").fetchone()[0]:
        raise CompatibilityError('头像表存在未适配触发器，禁止修改')
    schema=db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
    return {'schema_sha256':hashlib.sha256(json.dumps(schema,ensure_ascii=False).encode()).hexdigest(),
            'head_image_columns':[{'name':r[1],'type':r[2],'not_null':bool(r[3]),'primary_key':r[5]} for r in columns]}

def other_rows_digest(db,friend):
    digest=hashlib.sha256()
    # SQLite's IS NOT includes NULL, unlike <>. Do not omit nullable rows.
    for row in db.execute('SELECT * FROM head_image WHERE username IS NOT ? ORDER BY username',(friend,)):
        digest.update(b'ROW')
        for value in row:
            if value is None: kind,data=b'n',b''
            elif isinstance(value,bytes): kind,data=b'b',value
            elif isinstance(value,str): kind,data=b's',value.encode('utf-8')
            elif isinstance(value,int): kind,data=b'i',str(value).encode()
            elif isinstance(value,float): kind,data=b'f',struct.pack('<d',value)
            else: raise CompatibilityError('未知头像列数据类型')
            digest.update(kind+struct.pack('<Q',len(data))+data)
    return digest.hexdigest()
