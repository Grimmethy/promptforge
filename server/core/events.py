"""In-process event fan-out for the gallery (2026-11-08): when a generation finishes or an image is deleted, every open page is told so it can
add or drop the tile without a reload (owner principle: triggers, not polls). One bounded queue per subscriber; publishing never blocks, a
subscriber whose queue is full is dropped (its page reconnects and reconciles), and the number of subscribers is capped.
"""

import json
import logging
import queue
import threading

logger = logging.getLogger(__name__)

MAX_SUBSCRIBERS = 32
QUEUE_SIZE = 64

_lock = threading.Lock()
_subs = []


def subscribe():
    """Register a new subscriber queue, or return None when the cap is reached."""
    with _lock:
        if len(_subs) >= MAX_SUBSCRIBERS:
            return None
        q = queue.Queue(maxsize=QUEUE_SIZE)
        _subs.append(q)
        return q


def unsubscribe(q):
    with _lock:
        if q in _subs:
            _subs.remove(q)


def subscriber_count():
    with _lock:
        return len(_subs)


def publish(name, data):
    """Fan a named event out to every subscriber. Never blocks and never raises: a full queue drops that subscriber only."""
    try:
        frame = (str(name), json.dumps(data))
    except (TypeError, ValueError):
        logger.warning("events.publish: %s payload is not JSON-serialisable, dropped", name)
        return 0
    delivered = 0
    with _lock:
        for q in list(_subs):
            try:
                q.put_nowait(frame)
                delivered += 1
            except queue.Full:
                _subs.remove(q)
                logger.warning("events.publish: dropped a slow subscriber (queue full)")
    return delivered
