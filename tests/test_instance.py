import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest
from PyQt6.QtCore import QCoreApplication

from spotlight.instance import SingleInstance

ROOT = Path(__file__).resolve().parent.parent

CHILD = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {root!r})
    from PyQt6.QtCore import QCoreApplication
    from spotlight.instance import SingleInstance

    app = QCoreApplication([])
    instance = SingleInstance(sys.argv[1])
    assert instance.acquire(), "child could not acquire the instance"
    if sys.argv[2] == "obey":
        instance.quit_requested.connect(app.quit)
    print("ready", flush=True)
    sys.exit(app.exec())
    """
).format(root=str(ROOT))


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QCoreApplication.instance() or QCoreApplication([])


def start_child(name: str, behaviour: str) -> subprocess.Popen:
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD, name, behaviour], stdout=subprocess.PIPE, text=True
    )
    assert child.stdout.readline().strip() == "ready"
    return child


def test_first_instance_acquires_immediately():
    instance = SingleInstance(f"spotlight-test-{uuid.uuid4().hex}")
    assert instance.acquire(timeout_s=1)


def test_new_instance_replaces_one_that_obeys_the_quit_request():
    name = f"spotlight-test-{uuid.uuid4().hex}"
    old = start_child(name, "obey")
    try:
        new = SingleInstance(name)
        assert new.acquire(timeout_s=10)
        assert old.wait(timeout=10) == 0  # it left through a normal quit, not a kill
    finally:
        old.kill()


def test_an_instance_that_ignores_the_request_is_terminated():
    name = f"spotlight-test-{uuid.uuid4().hex}"
    old = start_child(name, "ignore")
    try:
        new = SingleInstance(name)
        assert new.acquire(timeout_s=0.5)
        assert old.wait(timeout=10) != 0
    finally:
        old.kill()
