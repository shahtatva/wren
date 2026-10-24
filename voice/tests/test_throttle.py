from app.throttle import SlidingWindow, allow_all
from tests.supabase_fakes import Clock


def test_a_window_allows_its_limit_then_refuses_until_time_passes():
    clock = Clock()
    window = SlidingWindow(2, 60.0, clock=clock)
    assert [window.allow("maya"), window.allow("maya"), window.allow("maya")] == [True, True, False]
    assert window.allow("dev")  # keys are counted apart
    clock.now += 60
    assert window.allow("maya")


def test_a_refused_request_does_not_use_up_the_budget():
    clock = Clock()
    per_email, overall = SlidingWindow(1, 600.0, clock=clock), SlidingWindow(2, 60.0, clock=clock)

    def attempt(email):
        return allow_all((per_email, email), (overall, "all"))

    assert attempt("maya@example.com")
    assert not attempt("maya@example.com")  # refused by her own window...
    assert attempt("dev@example.com")  # ...so it didn't count overall: there was room for this one
    assert not attempt("sam@example.com")  # refused overall...
    clock.now += 60  # the overall window empties; the per-email windows last 10 minutes
    assert attempt("sam@example.com")  # ...so it didn't count against Sam's own window
    assert not attempt("maya@example.com")
