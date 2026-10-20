"""Error categories only: never serialize raw exception messages or tracebacks."""
import re


def safe_error(exc):
    message=str(exc).lower()
    patterns=(('请先完全退出微信', 'wechat_process_still_running'),
              ('无法确认微信进程状态', 'wechat_process_status_unknown'),
              ('无法独占', 'exclusive_file_lock_failed'),
              ('存在未完成操作', 'pending_previous_operation'),
              ('残留组件身份或安装目录不匹配', 'helper_identity_or_installation_mismatch'),
              ('进程身份已变化', 'process_identity_changed'),
              ('无权结束残留组件', 'helper_termination_permission_denied'),
              ('无权核实残留组件', 'helper_identity_unverifiable'),
              ('新的微信进程', 'process_list_changed'),
              ('残留组件未在限时内退出', 'helper_exit_timeout'),
              ('电脑备注请输入', 'invalid_desktop_remark'),
              ('电脑备注不能包含', 'invalid_desktop_remark'),
              ('no such tokenizer', 'unsupported_sqlite_tokenizer'),
              ('unknown tokenizer', 'unsupported_sqlite_tokenizer'),
              ('no such collation', 'unsupported_sqlite_collation'),
              ('no such module', 'unsupported_sqlite_module'),
              ('database disk image is malformed', 'database_integrity_failed'),
              ('database is locked', 'database_locked'),
              ('database is busy', 'database_locked'),
              ('file is not a database', 'database_format_or_key_mismatch'),
              ('disk is full', 'disk_full'))
    category=next((code for phrase,code in patterns if phrase in message),None)
    if category is None:
        if isinstance(exc,PermissionError): category='filesystem_permission_denied'
        elif isinstance(exc,FileNotFoundError): category='file_not_found'
        elif isinstance(exc,OSError): category='filesystem_error'
        else: category='unclassified_error'
    report={'exception_type':type(exc).__name__,'error_category':category}
    from .windows import MAIN_PROCESSES, HELPER_PROCESSES
    blockers=[]
    for item in getattr(exc,'blocking_processes',()):
        name=getattr(item,'name',None);pid=getattr(item,'pid',None)
        if name in MAIN_PROCESSES|HELPER_PROCESSES and type(pid) is int and 0<pid<2**32:
            blockers.append({'name':name,'pid':pid,'role':'main' if name in MAIN_PROCESSES else 'helper'})
    if blockers: report['blocking_processes']=blockers[:64]
    code=getattr(exc,'sqlite_errorcode',None)
    if isinstance(code,int): report['sqlite_errorcode']=code
    name=getattr(exc,'sqlite_errorname',None)
    if isinstance(name,str) and re.fullmatch(r'SQLITE_[A-Z0-9_]+',name): report['sqlite_errorname']=name
    return report


def remark_failure(exc, *, version=None, gui_stage, service=None):
    outcome=getattr(service,'last_write_outcome','not_entered') if service else 'not_entered'
    report={'report_version':1,'operation':'desktop_remark_failure','wechat_version':version,
            'gui_stage':gui_stage,'backend_stage':getattr(service,'last_stage','not_entered') if service else 'not_entered',
            'source_write_outcome':outcome,
            'source_write_started':outcome in {'writing','rolled_back','recovery_required','written_verified'},
            'source_unchanged_by_this_operation':outcome in {'not_entered','not_started','not_written','rolled_back'},
            'client_rendering_verified':False,'remote_non_sync_verified':False,**safe_error(exc)}
    if gui_stage=='close_residual_helpers':
        report['source_unchanged_by_this_operation']=None
        report['source_state_requires_fresh_probe']=True
    underlying=getattr(service,'last_failure',None) if service else None
    if underlying:report['underlying_error']=underlying
    return report
