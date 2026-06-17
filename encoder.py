import threading


class TurnInterrupted(Exception):
    pass


_interrupt_event = threading.Event()
_pause_event = threading.Event()


def request_interrupt() -> None:
    _interrupt_event.set()
    _pause_event.clear()


def clear_interrupt() -> None:
    _interrupt_event.clear()


def is_interrupted() -> bool:
    return _interrupt_event.is_set()


def raise_if_interrupted() -> None:
    if is_interrupted():
        raise TurnInterrupted()


def request_pause() -> None:
    if not is_interrupted():
        _pause_event.set()


def request_resume() -> None:
    _pause_event.clear()


def is_paused() -> bool:
    return _pause_event.is_set()
