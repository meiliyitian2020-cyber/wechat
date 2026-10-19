"""SQLCipher4 WAL validation (SQLite fileformat2.html#walformat).

Validate header/frame checksums, transaction boundaries and each valid frame's
SQLCipher HMAC. Old-epoch frames after a salt change are not part of this WAL.
No original file is modified here.
"""
import hashlib
import hmac
import struct
from .cipher_stage import CipherError

PAGE=4096
FRAME=24+PAGE

def checksum(data,seed,endian):
    if len(data)%8: raise CipherError('WAL 校验数据长度无效')
    s1,s2=seed
    for first,second in struct.iter_unpack(endian+'II',data):
        s1=(s1+first+s2)&0xffffffff
        s2=(s2+second+s1)&0xffffffff
    return s1,s2

def validate_wal(main,wal,key):
    result={'wal_frames':0,'committed_wal_frames':0,'uncommitted_wal_frames':0,'stale_wal_tail':False}
    if not wal: return result
    if len(wal)<32: raise CipherError('WAL 文件头不完整，已停止操作并保留原文件')
    magic,version,page_size=struct.unpack('>III',wal[:12])
    if magic not in (0x377f0682,0x377f0683) or version!=3007000 or page_size!=PAGE:
        raise CipherError('WAL 格式尚未适配，已停止操作并保留原文件')
    endian='>' if magic&1 else '<'
    rolling=checksum(wal[:24],(0,0),endian)
    if rolling!=struct.unpack('>II',wal[24:32]): raise CipherError('WAL 文件头校验失败，未改动原文件')
    mac_salt=bytes(v^0x3a for v in main[:16])
    mac_key=hashlib.pbkdf2_hmac('sha512',key,mac_salt,2,32)
    salt=wal[16:24]
    offset=32
    while offset<len(wal):
        if len(wal)-offset<24: raise CipherError('WAL 尾部不完整，未改动原文件')
        header=wal[offset:offset+24]
        if header[8:16]!=salt:
            # SQLite recovery stops at the first frame from an earlier epoch.
            result['stale_wal_tail']=True
            break
        if len(wal)-offset<FRAME: raise CipherError('WAL 数据帧不完整，未改动原文件')
        pgno,dbsize=struct.unpack('>II',header[:8])
        if pgno==0: raise CipherError('WAL 页编号无效')
        payload=wal[offset+24:offset+FRAME]
        rolling=checksum(header[:8]+payload,rolling,endian)
        if rolling!=struct.unpack('>II',header[16:24]): raise CipherError('WAL 数据帧校验失败，未改动原文件')
        body=payload[16 if pgno==1 else 0:4032]+struct.pack('<I',pgno)
        if not hmac.compare_digest(hmac.new(mac_key,body,hashlib.sha512).digest(),payload[-64:]):
            raise CipherError('WAL 加密页完整性校验失败，未改动原文件')
        result['wal_frames']+=1
        if dbsize: result['committed_wal_frames']=result['wal_frames']
        offset+=FRAME
    result['uncommitted_wal_frames']=result['wal_frames']-result['committed_wal_frames']
    return result
