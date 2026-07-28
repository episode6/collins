from collins.window import blast_radius


def _breakdown(count: int, hidden: int = 2, total: int = 2):
    return [(f"project-{i:03d}", hidden, total) for i in range(count)]


def test_blast_radius_names_every_project_when_it_fits():
    radius = blast_radius(6, _breakdown(3))
    assert "project-000 — 2 of 2" in radius.detail
    assert "project-002 — 2 of 2" in radius.detail
    assert "other project" not in radius.detail
    assert "3 of these project(s) lose every session they have." in radius.detail


def test_blast_radius_names_a_full_short_list():
    # Seven still fits: naming them all beats cutting to four plus a summary
    # line for the three that are left.
    radius = blast_radius(14, _breakdown(7))
    assert "project-006 — 2 of 2" in radius.detail
    assert "other project" not in radius.detail


def test_blast_radius_caps_the_list_once_it_grows():
    radius = blast_radius(16, _breakdown(8))
    named = [line for line in radius.detail.splitlines() if line.startswith("project-")]
    assert len(named) == 4
    assert "…and 4 other project(s) — 8 session(s)" in radius.detail


def test_blast_radius_stays_the_same_size_at_scale():
    radius = blast_radius(400, _breakdown(200))
    named = [line for line in radius.detail.splitlines() if line.startswith("project-")]
    assert len(named) == 4  # 200 projects must not grow the dialog
    assert "…and 196 other project(s) — 392 session(s)" in radius.detail
    assert "400 session(s) in 200 project(s)" in radius.summary


def test_blast_radius_keeps_the_project_list_out_of_the_summary():
    # The summary is the dialog's body, the detail a left-aligned child: the
    # per-project column must not leak back into the centred half, and the
    # detail must start on the list rather than on a blank line.
    radius = blast_radius(6, _breakdown(3))
    assert "project-000" not in radius.summary
    assert radius.detail.splitlines()[0] == "project-000 — 2 of 2"


def test_blast_radius_omits_the_warning_when_no_project_empties():
    radius = blast_radius(3, [("alpha", 3, 9)])
    assert "alpha — 3 of 9" in radius.detail
    assert "lose every session" not in radius.detail
