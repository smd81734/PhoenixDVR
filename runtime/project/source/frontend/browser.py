"""Select the newest numeric vendor Chrome executable in a staged browser bundle."""
import re
from pathlib import Path
PATTERN=re.compile(r'^chrome\.(\d+(?:\.\d+)*)$')
def version(path):
    match=PATTERN.fullmatch(Path(path).name)
    return tuple(int(p) for p in match.group(1).split('.')) if match else None
def newest(paths):
    choices=[Path(p) for p in paths if version(p) is not None and Path(p).is_file()]
    if not choices:raise ValueError('No versioned Chrome executable found')
    return max(choices,key=lambda p:(version(p),p.as_posix()))
def select(directory):return newest((Path(directory)/'bin').glob('chrome.*'))
