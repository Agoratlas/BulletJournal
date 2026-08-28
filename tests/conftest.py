from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'

# Tests exercise the bundled template catalog only, regardless of developer plugins.
os.environ['BULLETJOURNAL_DISABLE_EXTERNAL_TEMPLATE_PROVIDERS'] = '1'

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

existing_pythonpath = os.environ.get('PYTHONPATH')
pythonpath_entries = [] if not existing_pythonpath else existing_pythonpath.split(os.pathsep)
if str(SRC) not in pythonpath_entries:
    os.environ['PYTHONPATH'] = os.pathsep.join([str(SRC), *pythonpath_entries]) if pythonpath_entries else str(SRC)
