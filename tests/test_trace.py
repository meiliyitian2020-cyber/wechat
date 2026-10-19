import json
from editor.trace import Timeline, LoginTrace


def row():
    return dict(desired_avatar=b'private-custom', original_avatar=b'private-original',
                last_applied=True, account='private-account', friend='private-friend')


def details(image, md5='private-md5', timestamp=123):
    return dict(image=image, md5=md5, update_time=timestamp)


def test_timeline_distinguishes_overwrite_from_temporary_read_failure_and_redacts():
    timeline = Timeline(row(), '4.1.15.56')
    timeline.add(0, details(b'private-custom'), False)
    timeline.add(2, None, True)
    assert timeline.report('test')['replacement_after_custom_observed'] is False
    timeline.add(4, details(b'private-other', 'private-new-md5', 234), True)
    report = timeline.report('test')
    assert report['custom_bytes_observed'] is True
    assert report['replacement_after_custom_observed'] is True
    assert report['read_failures'] == 1
    assert report['events'][-1]['md5_changed_since_last_read'] is True
    assert report['events'][-1]['image_changed_since_last_read'] is True
    assert report['events'][-1]['update_time_changed_since_last_read'] is True
    assert report['client_rendering_verified'] is False
    serialized = json.dumps(report)
    for private in ('private-custom', 'private-original', 'private-other', 'private-md5',
                    'private-new-md5', 'private-account', 'private-friend'):
        assert private not in serialized


def test_unchanged_samples_are_compacted_without_losing_sample_count():
    timeline = Timeline(row(), '4.1.15.56')
    for n in range(90):
        timeline.add(n * 2, details(b'private-custom'), True)
    report = timeline.report('time_limit')
    assert report['samples'] == 90
    assert len(report['events']) <= 2
    assert report['replacement_after_custom_observed'] is False


def test_sampler_is_bounded_and_stops_without_leaving_thread_or_exposing_errors():
    timeline = Timeline(row(), '4.1.15.56')
    logical_time = [0.0]
    def unavailable(*args):
        logical_time[0] = 1.0
        raise RuntimeError('private-secret-error')
    worker = LoginTrace(object(), 'private-friend', timeline, reader=unavailable,
                        process=lambda:True, interval=.005, duration=.03, clock=lambda:logical_time[0])
    worker.start()
    assert worker.wait(1000)
    assert not worker.isRunning()
    assert worker.context is None
    assert worker.result['stop_reason'] == 'time_limit'
    assert worker.result['read_failures'] > 0
    assert 'private-secret-error' not in json.dumps(worker.result)
    worker = LoginTrace(object(), 'private-friend', Timeline(row(), '4.1.15.56'),
                        reader=unavailable, process=lambda:False, interval=10)
    worker.start(); worker.stop()
    assert worker.wait(1000)
    assert worker.result['stop_reason'] == 'user_finished'
