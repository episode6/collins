from collins.window import blast_radius_body


def _breakdown(count: int, hidden: int = 2, total: int = 2):
    return [(f"project-{i:03d}", hidden, total) for i in range(count)]


def test_blast_radius_names_every_project_when_it_fits():
    body = blast_radius_body(6, _breakdown(3))
    assert "project-000 — 2 of 2" in body
    assert "project-002 — 2 of 2" in body
    assert "other project" not in body
    assert "3 of these project(s) lose every session they have." in body


def test_blast_radius_names_a_lone_leftover_instead_of_summing_it():
    # 5 projects, 4 rows: summarising a single project as "…and 1 other" reads
    # worse than just naming it.
    body = blast_radius_body(10, _breakdown(5))
    assert "project-004 — 2 of 2" in body
    assert "other project" not in body


def test_blast_radius_caps_the_list_and_sums_the_rest():
    body = blast_radius_body(400, _breakdown(200))
    named = [line for line in body.splitlines() if line.startswith("project-")]
    assert len(named) == 4  # 200 projects must not grow the dialog
    assert "…and 196 other project(s) — 392 session(s)" in body
    assert "400 session(s) in 200 project(s)" in body


def test_blast_radius_omits_the_warning_when_no_project_empties():
    body = blast_radius_body(3, [("alpha", 3, 9)])
    assert "alpha — 3 of 9" in body
    assert "lose every session" not in body
