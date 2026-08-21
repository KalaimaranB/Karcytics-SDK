import logging

import pytest

from karcytics_sdk.plugin.logging import _NOISY_THIRD_PARTY_LOGGERS, configure_plugin_logging


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """configure_plugin_logging() mutates the root logger process-wide and
    is deliberately idempotent (guarded by an attribute on the root logger
    itself) — both effects must be undone after each test or later tests
    (here and in unrelated modules sharing this process) would see stale
    handlers or skip real configuration because the guard looks already-set.
    It also pins known-noisy third-party loggers to WARNING; those levels
    must be restored too for the same reason.
    """
    root = logging.getLogger()
    original_handlers = root.handlers[:]
    original_level = root.level
    original_noisy_levels = {name: logging.getLogger(name).level for name in _NOISY_THIRD_PARTY_LOGGERS}
    try:
        yield
    finally:
        for h in root.handlers[:]:
            root.removeHandler(h)
            h.close()
        for h in original_handlers:
            root.addHandler(h)
        root.setLevel(original_level)
        for name, level in original_noisy_levels.items():
            logging.getLogger(name).setLevel(level)
        for attr in ("_karcytics_plugin_logging_configured", "_karcytics_plugin_log_file"):
            if hasattr(root, attr):
                delattr(root, attr)


def test_configure_plugin_logging_creates_a_rotating_file_under_plugin_workers(tmp_path):
    log_dir = tmp_path / "logs" / "plugin_workers"

    log_file = configure_plugin_logging("flow_cytometry", log_dir=log_dir)

    assert log_file == log_dir / "flow_cytometry.log"

    logging.getLogger("some.module").debug("hello from the worker")
    for h in logging.getLogger().handlers:
        h.flush()

    assert "hello from the worker" in log_file.read_text(encoding="utf-8")


def test_configure_plugin_logging_is_idempotent(tmp_path):
    log_dir = tmp_path / "logs" / "plugin_workers"

    first = configure_plugin_logging("flow_cytometry", log_dir=log_dir)
    handler_count_after_first = len(logging.getLogger().handlers)
    second = configure_plugin_logging("flow_cytometry", log_dir=log_dir)

    assert first == second
    assert len(logging.getLogger().handlers) == handler_count_after_first


def test_configure_plugin_logging_silences_noisy_third_party_compiler_logs(tmp_path):
    for name in _NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)

    configure_plugin_logging("flow_cytometry", log_dir=tmp_path / "logs" / "plugin_workers")

    for name in _NOISY_THIRD_PARTY_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING
