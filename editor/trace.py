"""Bounded, in-process selected-avatar sampler. Not an OS file-I/O trace."""
import hashlib
import time
from threading import Event
import psutil
from PySide6.QtCore import QThread, Signal
from .diagnostics import login_report
from .observe import read_details
from .offline import WRITE_STRATEGY


def wechat_running():
    try:
        for p in psutil.process_iter(['name']):
            if (p.info['name'] or '').lower() in {'weixin.exe', 'wechat.exe'}:
                return True
        return False
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        return None


class Timeline:
    def __init__(self, row, version):
        self.row, self.version = row, version
        self.events = []
        self.previous = None
        self.samples = 0
        self.failures = 0

    def add(self, elapsed, details, running):
        self.samples += 1
        if details is None:
            self.failures += 1
        report = login_report(self.row, None if details is None else details['image'], self.version)
        event = {'elapsed_seconds': round(elapsed, 1), 'wechat_process_running': running,
                 'observation': report['observation']}
        if details is not None:
            image, stored = details['image'], details['md5']
            event['md5_matches_image_bytes'] = (isinstance(stored, str) and
                                                stored.lower() == hashlib.md5(image).hexdigest())
            if self.previous is not None:
                event['image_changed_since_last_read'] = image != self.previous['image']
                event['md5_changed_since_last_read'] = stored != self.previous['md5']
                event['update_time_changed_since_last_read'] = details['update_time'] != self.previous['update_time']
            # Values remain only in RAM, never in the report or logs.
            self.previous = details
        signature = {k:v for k,v in event.items() if k != 'elapsed_seconds'}
        previous_signature = ({k:v for k,v in self.events[-1].items() if k != 'elapsed_seconds'}
                              if self.events else None)
        if signature != previous_signature:
            self.events.append(event)

    def report(self, reason):
        observed = [e['observation'] for e in self.events]
        custom_seen = False
        replaced_after_custom = False
        for state in observed:
            if state == 'desired_bytes_present':
                custom_seen = True
            elif custom_seen and state != 'unverifiable':
                replaced_after_custom = True
        return {'report_version': 3, 'write_strategy': WRITE_STRATEGY, 'operation': 'guided_login_timeline',
                'wechat_version': self.version, 'stop_reason': reason,
                'sampling_interval_seconds': 2, 'samples': self.samples, 'read_failures': self.failures,
                'custom_bytes_observed': custom_seen,
                'replacement_after_custom_observed': replaced_after_custom,
                'client_rendering_verified': False,
                'source_written_by_sampler': False, 'guided_trial_write_performed': True,
                'scope': '仅选定好友头像库快照轮询；可能遗漏两次采样之间的变化。不是系统文件读写跟踪，不能归因到具体进程或证明下载、校验机制。',
                'events': self.events}


class LoginTrace(QThread):
    progress = Signal(int)
    def __init__(self, context, friend, timeline, *, reader=read_details, process=wechat_running,
                 interval=2, duration=180, clock=time.monotonic):
        super().__init__()
        self.context, self.friend, self.timeline = context, friend, timeline
        self.reader, self.process = reader, process
        self.interval, self.duration = interval, duration
        self.clock = clock
        self.stop_event = Event()
        self.result = None

    def stop(self):
        self.stop_event.set()

    def run(self):
        start = self.clock()
        try:
            while not self.stop_event.wait(self.interval):
                elapsed = self.clock() - start
                if elapsed >= self.duration:
                    break
                try:
                    details = self.reader(self.context, self.friend)
                except Exception:
                    details = None
                try:
                    running = self.process()
                except Exception:
                    running = None
                self.timeline.add(elapsed, details, running)
                self.progress.emit(int(elapsed))
            self.result = self.timeline.report('user_finished' if self.stop_event.is_set() else 'time_limit')
        finally:
            # QThread finished signal is emitted only after run returns.
            self.context = None
