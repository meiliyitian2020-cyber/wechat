"""Redacted post-login evidence. No contact identifiers, images, paths or keys."""
from .core import digest


def login_report(row, current, version):
    desired = row['desired_avatar']
    original = row['original_avatar']
    if current is None:
        result = 'unverifiable'
        explanation = '无法读取当前头像库，不能判断是否被覆盖。'
    elif desired is not None and digest(current) == digest(desired):
        result = 'desired_bytes_present'
        explanation = '当前头像库仍是自定义图片；若客户端显示原头像，说明此记录不足以控制实际显示。缓存优先级尚未确认。'
    elif not current:
        result = 'record_missing_or_empty'
        explanation = '当前头像记录已不存在或为空；不能据此判断同步机制。'
    elif original is not None and digest(current) == digest(original):
        result = 'original_bytes_present'
        explanation = '当前头像库与保存的原头像一致，自定义记录已不在此处；不能证明是校验重下载导致。'
    else:
        result = 'other_bytes_present'
        explanation = '当前头像库为另一张图片，自定义记录已不在此处；变化来源尚未确认。'
    return {'report_version': 1, 'operation': 'post_login_observation',
            'wechat_version': version, 'prior_database_write_recorded': bool(row['last_applied']),
            'observation': result, 'client_rendering_verified': False,
            'source_written': False, 'scope': explanation}
