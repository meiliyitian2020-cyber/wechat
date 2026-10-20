import json
from types import SimpleNamespace
import pytest
from editor.errors import safe_error, remark_failure


@pytest.mark.parametrize('message,category', [
    ('no such tokenizer: secret-tokenizer-name','unsupported_sqlite_tokenizer'),
    ('no such module: secret-module','unsupported_sqlite_module'),
    ('no such collation sequence: private-collation','unsupported_sqlite_collation'),
    ('private-key private-path private-contact','unclassified_error'),
])
def test_error_categories_never_export_raw_exception_or_details(message,category):
    error=RuntimeError(message)
    result=safe_error(error)
    assert result['error_category']==category
    assert 'private' not in json.dumps(result) and 'secret' not in json.dumps(result)
    assert message not in json.dumps(result)


@pytest.mark.parametrize('outcome,started,unchanged',[
    ('not_written',False,True),('rolled_back',True,True),
    ('written_verified',True,False),('recovery_required',True,False),('writing',True,False)])
def test_source_state_is_not_misrepresented_after_success_or_rollback(outcome,started,unchanged):
    service=SimpleNamespace(last_write_outcome=outcome,last_stage='safe_stage',last_failure=None)
    report=remark_failure(RuntimeError('private'),version='4.1.15.56',gui_stage='save_write_history',service=service)
    assert report['source_write_started'] is started
    assert report['source_unchanged_by_this_operation'] is unchanged


def test_failed_table_context_exports_only_anonymous_fields():
    service=SimpleNamespace(last_write_outcome='not_written',last_stage='remark_other_tables_digest_before',
                            last_failure=None,digest_context=dict(table_ordinal=7,column_count=15,
                                table_is_contact=False,table_is_virtual=True,query_stage='row_read',
                                table_name='private-table',private_path='private-path'))
    report=remark_failure(RuntimeError('private-data'),version='4.1.15.56',gui_stage='copy_probe',service=service)
    assert report['table_check_context']==dict(table_ordinal=7,column_count=15,
                                            table_is_contact=False,table_is_virtual=True,query_stage='row_read')
    assert 'private-' not in json.dumps(report)
