from collins.window import blast_radius_body


def _breakdown(count: int, hidden: int = 2, total: int = 2):
    return [(f"project-{i:03d}", hidden, total) for i in range(count)]


def _list_lines(body: str) -> list[str]:
    """The per-project block: the paragraph above it and the warning below are
    separated by blank lines."""
    return body.split("\n\n")[1].splitlines()


def _named(body: str) -> list[str]:
    """The same block minus the "…and N others" line, which sums up the
    projects the list didn't name."""
    return [line for line in _list_lines(body) if not line.startswith("…")]


def test_blast_radius_names_every_project_when_it_fits():
    body = blast_radius_body(6, _breakdown(3))
    assert "project-000 — 2 of 2" in body
    assert "project-002 — 2 of 2" in body
    assert "other project" not in body
    assert "3 of these project(s) lose every session they have." in body


def test_blast_radius_names_a_full_short_list():
    # Seven still fits: naming them all beats cutting to four plus a summary
    # line for the three that are left.
    body = blast_radius_body(14, _breakdown(7))
    assert "project-006 — 2 of 2" in body
    assert "other project" not in body


def test_blast_radius_caps_the_list_once_it_grows():
    body = blast_radius_body(16, _breakdown(8))
    assert len(_named(body)) == 4
    assert "…and 4 other project(s) — 8 session(s)" in body


def test_blast_radius_stays_the_same_size_at_scale():
    body = blast_radius_body(400, _breakdown(200))
    assert len(_named(body)) == 4  # 200 projects must not grow the dialog
    assert "…and 196 other project(s) — 392 session(s)" in body
    assert "400 session(s) in 200 project(s)" in body


def test_blast_radius_orders_the_lines_shortest_first():
    # Centred in the dialog, the column tapers evenly instead of jumping about.
    # Which projects get named still goes by session count, not by length.
    breakdown = [("epsilon-web-frontend", 9, 9), ("beta", 4, 4), ("gamma-api", 2, 2)]
    assert _named(blast_radius_body(15, breakdown)) == [
        "beta — 4 of 4",
        "gamma-api — 2 of 2",
        "epsilon-web-frontend — 9 of 9",
    ]


def test_blast_radius_keeps_the_summed_line_last():
    # It stands for the projects the named lines left out, however short it is.
    breakdown = [(f"a-project-with-a-long-name-{i}", 5, 5) for i in range(8)]
    assert _list_lines(blast_radius_body(40, breakdown))[-1] == (
        "…and 4 other project(s) — 20 session(s)"
    )


def test_blast_radius_omits_the_warning_when_no_project_empties():
    body = blast_radius_body(3, [("alpha", 3, 9)])
    assert "alpha — 3 of 9" in body
    assert "lose every session" not in body
