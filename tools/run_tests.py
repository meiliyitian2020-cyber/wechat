"""Run the unchanged suite, retaining its exit code and exposing CI failures."""
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


def main():
    with tempfile.TemporaryDirectory(prefix='wechat-test-results-') as temp:
        report = Path(temp) / 'junit.xml'
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q', f'--junitxml={report}'])
        if result.returncode and report.exists():
            root = ET.parse(report).getroot()
            for case in root.iter('testcase'):
                failure = case.find('failure')
                if failure is None:
                    failure = case.find('error')
                if failure is not None:
                    message = (failure.text or failure.get('message', 'Test failed'))[-10000:]
                    message = message.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
                    print(f"::error title={case.get('name', 'Test failed')}::{message}", flush=True)
        return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
