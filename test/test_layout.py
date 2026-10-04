"""Package relocation must not relocate user data or break the app entry point."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class LayoutTests(unittest.TestCase):
    def test_imports_and_persistent_paths_from_another_working_directory(self):
        root = Path(__file__).resolve().parent.parent
        program = '''
import json
import app
from backend.paths import PROJECT_ROOT
from backend.data.nba import CACHE_PATH
from backend.assistant.tools import TOOLS
from backend.debate.tools import DEBATE_TOOLS
print(json.dumps({'root':str(PROJECT_ROOT), 'cache':str(CACHE_PATH),
                  'history':str(app.HISTORY_DIR), 'frontend':str(app.FRONTEND_DIST),
                  'tools':bool(TOOLS and DEBATE_TOOLS)}))
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-c', program], cwd=directory,
                                    env={**os.environ, 'PYTHONPATH':str(root)},
                                    check=True, capture_output=True, text=True, timeout=30)
        paths = json.loads(result.stdout)
        self.assertEqual(paths['root'],str(root))
        self.assertEqual(paths['cache'],str(root/'data_cache'/'nba.sqlite3'))
        self.assertEqual(paths['history'],str(root/'chat_history'))
        self.assertEqual(paths['frontend'],str(root/'frontend'/'dist'))
        self.assertTrue(paths['tools'])
