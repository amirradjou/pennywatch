from pennywatch import builds, stacks


def _releases():
    def files(date):
        return [{"upload_time": f"{date}T04:00:00"}]

    return {
        "0.16.0.dev9": files("2026-08-08"),
        "0.16.0.dev10": files("2026-08-09"),
        "0.16.0.dev11": [],
        "0.16.0.dev8": files("2026-08-07"),
        "0.15.0": files("2026-09-01"),
    }


def test_only_dev_builds_since_the_date_with_files_left_oldest_first():
    assert builds.dev_builds(_releases(), "2026-08-08") == ["0.16.0.dev9", "0.16.0.dev10"]


def test_pending_goes_newest_first_skips_what_is_done_and_stops_at_the_limit(monkeypatch):
    monkeypatch.setattr(stacks, "fetch_json", lambda url: {"releases": _releases()})
    assert builds.pending(set(), "2026-08-01", 2) == ["0.16.0.dev10", "0.16.0.dev9"]
    assert builds.pending({"0.16.0.dev10"}, "2026-08-01", 2) == ["0.16.0.dev9", "0.16.0.dev8"]
    assert builds.pending({"0.16.0.dev10", "0.16.0.dev9", "0.16.0.dev8"}, "2026-08-01", 2) == []
