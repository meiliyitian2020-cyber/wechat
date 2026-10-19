import pytest
from editor.windows import check_read_version, check_write_version

@pytest.mark.parametrize('version',['4.1.13.65','4.1.15.65'])
def test_explicit_versions_can_read(version):
    check_read_version(version,version)

@pytest.mark.parametrize('version',['4.1.15.66','4.1.16.1','4.1.13.66',''])
def test_unknown_versions_cannot_read_or_write(version):
    with pytest.raises(RuntimeError): check_read_version(version)
    with pytest.raises(RuntimeError): check_write_version(version,version)

def test_new_version_is_not_blindly_write_enabled():
    with pytest.raises(RuntimeError,match='尚未开放'):
        check_write_version('4.1.15.65','4.1.15.65')
    check_write_version('4.1.13.65','4.1.13.65')

def test_update_after_loading_context_is_rejected():
    with pytest.raises(RuntimeError,match='版本变化'):
        check_read_version('4.1.15.65','4.1.13.65')
    with pytest.raises(RuntimeError):
        check_write_version('4.1.13.65','4.1.15.65')
