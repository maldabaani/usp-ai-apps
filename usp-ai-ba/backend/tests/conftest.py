"""Sets JOBS_DIR/JWT_SECRET to an isolated temp directory before anything
imports config.py (which reads them at module-import time via
_default_jwt_secret()/os.getenv), so the test suite never touches the real
backend/jobs/ directory or depends on whatever secret a developer's own
running server happens to have generated.
"""
import os
import tempfile

_TEST_JOBS_DIR = tempfile.mkdtemp(prefix="storyforge-test-jobs-")
os.environ.setdefault("JOBS_DIR", _TEST_JOBS_DIR)
os.environ.setdefault("JWT_SECRET", "test-suite-shared-secret-not-for-production-use")
