import builtins
import time

from jurigged import codefile
from jurigged.live import WatchOperation, default_logger, watch
from jurigged.register import Registry

from .common import one_test_per_assert
from .test_codefile import apple_file as apple, tmod  # noqa


def _capture(obj):
    value = []
    old_print = print
    builtins.print = lambda *args: value.append(" ".join(map(str, args)))
    default_logger(obj)
    builtins.print = old_print
    return value[0]


def _std(cls, cf, lineno):
    return _capture(cls(codefile=cf, definition=cf.defnmap[lineno]))


@one_test_per_assert
def test_logger(apple):
    assert (
        _std(codefile.UpdateOperation, apple, 23)
        == "Update tests.snippets.apple.Orchard.cortland @L23"
    )
    assert (
        _std(codefile.AddOperation, apple, 23)
        == "Add tests.snippets.apple.Orchard.cortland @L23"
    )
    assert (
        _std(codefile.AddOperation, apple, 42)
        == "Run tests.snippets.apple @L42: from functools import wraps"
    )
    assert (
        _std(codefile.DeleteOperation, apple, 23)
        == "Delete tests.snippets.apple.Orchard.cortland @L23"
    )
    assert (
        _std(codefile.FailedUpdateOperation, apple, 23)
        == "Failed update tests.snippets.apple.Orchard.cortland @L23"
    )
    assert _capture(WatchOperation("some_file.py")) == "Watch some_file.py"
    assert "TypeError" in _capture(TypeError("hello"))
    assert "SyntaxError" in _capture(SyntaxError("oh no"))
    assert _capture(12345) == "12345"


def test_watch(tmod):
    mangle = "_5"
    registry = Registry()
    watcher = watch(pattern=tmod.rel("*.py"), registry=registry)
    za = tmod.imp("za", mangle=mangle)
    assert za.word == "tyrant"

    # This one is a syntax error, but it shouldn't kill the thread
    tmod.write("za_5.py", 'word = "pirate\n')
    # If the FS is slow 0.05 seconds might not be enough, but oh well
    time.sleep(0.05)
    assert za.word == "tyrant"

    # This one is OK and the change should be loaded
    tmod.write("za_5.py", 'word = "pirate"\n')
    time.sleep(0.05)
    assert za.word == "pirate"

    watcher.stop()
    # Updates won't reload anymore
    tmod.write("za_5.py", 'word = "nowatch"\n')
    time.sleep(0.05)
    assert za.word == "pirate"

    watcher.join()
    assert not watcher.observer.is_alive()