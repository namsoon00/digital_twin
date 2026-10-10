"""Keep a slow proposal/compiler request owned until its fenced completion."""
from contextlib import contextmanager
from threading import Event, Thread


@contextmanager
def proposal_lease(store, request):
    stopped, failures = Event(), []

    def renew():
        while not stopped.wait(45):
            try:
                if not store.renew_hypothesis_proposal_request(request):
                    raise RuntimeError("hypothesis proposal lease lost")
            except Exception as error:
                failures.append(error)
                break

    if not store.renew_hypothesis_proposal_request(request):
        raise RuntimeError("hypothesis proposal lease lost")
    worker = Thread(target=renew, name="hypothesis-proposal-lease", daemon=True)
    worker.start()
    try:
        yield
        if failures:
            raise failures[0]
    finally:
        stopped.set()
        worker.join(timeout=1)
