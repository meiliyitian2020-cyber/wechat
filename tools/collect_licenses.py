"""Copy installed runtime distributions' supplied license/notice files."""
from importlib.metadata import distribution
from pathlib import Path

out=Path('third_party/runtime')
for name in ['PySide6','PySide6-Essentials','PySide6-Addons','shiboken6','Pillow',
             'cryptography','cffi','pycparser','psutil','sqlcipher3','pyinstaller']:
    dist=distribution(name)
    for entry in dist.files or []:
        if not any(word in str(entry).lower() for word in ['license','notice','copying']): continue
        source=Path(dist.locate_file(entry))
        if not source.is_file(): continue
        relative=Path(name)/str(entry).replace('..','_').replace(':','_')
        target=out/relative;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(source.read_bytes())
