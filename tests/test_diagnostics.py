import json
import pytest
from editor.diagnostics import login_report


@pytest.mark.parametrize('current,result', [
    (None, 'unverifiable'), (b'new-private', 'desired_bytes_present'),
    (b'old-private', 'original_bytes_present'), (b'', 'record_missing_or_empty'),
    (b'other-private', 'other_bytes_present')])
def test_post_login_evidence_is_scoped_and_redacted(current, result):
    row = dict(desired_avatar=b'new-private', original_avatar=b'old-private',
               last_applied='time', friend='private-contact', account='private-account')
    report = login_report(row, current, '4.1.15.56')
    assert report['observation'] == result
    assert report['source_written'] is False
    assert report['client_rendering_verified'] is False
    serialized = json.dumps(report)
    for secret in ('new-private', 'old-private', 'other-private', 'private-contact', 'private-account'):
        assert secret not in serialized
