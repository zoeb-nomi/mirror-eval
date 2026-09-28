"""metrics.py against small synthetic call records — no run.py needed."""

import metrics

CANON = {
    "owned_sources": ["zoebnomi.com", "github.com/zoeb-nomi"],
    "self_published_sources": ["linkedin.com/in/zoebnomi"],
    "fields": [
        {"id": "current_company", "truth": True,
         "verifying_url": "https://rocketreach.co/zoeb-nomi-email_380343494",
         "match_all": ["Instead"],
         "match_any": ["instead.com", "tax", "Instead Inc", "Instead, a"]},
        {"id": "current_title", "truth": False, "verifying_url": None},
    ],
}


def _rec(**kw):
    base = {
        "task": "lookup", "stack": "anthropic", "condition": "full", "rep": 1,
        "prompt_id": "p1", "answer_text": "", "fetched_urls": [], "claims": [],
        "parse_ok": True, "error": None, "cost_usd_est": 0.01,
    }
    base.update(kw)
    return base


def test_owned_surface_hit_rate():
    records = [
        _rec(fetched_urls=["https://zoebnomi.com/about"]),
        _rec(fetched_urls=["https://example.com/x"]),
    ]
    out = metrics.owned_surface_hit_rate(records, CANON)
    assert out["lookup|anthropic|full"]["hit_rate"] == 0.5
    assert out["lookup|anthropic|full"]["n_calls"] == 2


def test_owned_surface_respects_path_prefix_for_github():
    # github.com/zoeb-nomi/foo counts; github.com/someone-else does not
    records = [
        _rec(fetched_urls=["https://github.com/zoeb-nomi/crosssource"]),
        _rec(fetched_urls=["https://github.com/someone-else/repo"]),
    ]
    out = metrics.owned_surface_hit_rate(records, CANON)
    assert out["lookup|anthropic|full"]["hit_rate"] == 0.5


def test_field_accuracy_only_scores_truth_fields():
    records = [_rec(answer_text="Zoeb works at Instead, a tax platform, as a PM.")]
    out = metrics.field_accuracy(records, CANON)
    assert out["current_company"]["scored"] is True
    assert out["current_company"]["by_group"]["lookup|anthropic|full"]["accuracy"] == 1.0
    # current_title has truth: False ("claim") — field_accuracy only scores
    # truth fields, so a claim field never appears in its output at all.
    assert "current_title" not in out


def test_hops_to_truth_finds_first_owned_or_verifying_url():
    records = [_rec(fetched_urls=["https://irrelevant.com/x", "https://zoebnomi.com/about"])]
    out = metrics.hops_to_truth(records, CANON)
    assert out["lookup|anthropic|full"]["min_hop"] == 2
    assert out["lookup|anthropic|full"]["no_hit_rate"] == 0.0


def test_hops_to_truth_no_hit():
    records = [_rec(fetched_urls=["https://irrelevant.com/x"])]
    out = metrics.hops_to_truth(records, CANON)
    assert out["lookup|anthropic|full"]["no_hit_rate"] == 1.0
    assert out["lookup|anthropic|full"]["min_hop"] is None


def test_label_distribution_is_min_max_across_reps_not_a_mean():
    records = [
        _rec(rep=1, claims=[{"text": "x", "source_url": "https://a.com/1", "label": "verified"}]),
        _rec(rep=1, claims=[{"text": "y", "source_url": "https://a.com/2", "label": "verified"}]),
        _rec(rep=2, claims=[{"text": "z", "source_url": "https://a.com/3", "label": "verified"}]),
    ]
    out = metrics.label_distribution(records)
    # rep 1 has 2 verified claims on a.com, rep 2 has 1 -> min=1, max=2
    assert out["a.com"]["verified"]["min"] == 1
    assert out["a.com"]["verified"]["max"] == 2
    assert out["a.com"]["verified"]["n_reps_seen"] == 2


def test_parse_failure_rate():
    records = [_rec(parse_ok=True), _rec(parse_ok=False), _rec(parse_ok=False)]
    out = metrics.parse_failure_rate(records)
    assert out["lookup|anthropic|full"]["parse_failure_rate"] == round(2 / 3, 4)


def test_parse_failure_ignores_errored_calls():
    records = [_rec(parse_ok=False, error="HTTPError: 500")]
    out = metrics.parse_failure_rate(records)
    # errored calls are excluded upstream by compute_all, but parse_failure_rate
    # itself trusts its caller; verify compute_all does the exclusion.
    result = metrics.compute_all(records, CANON)
    assert result["n_errored"] == 1
    assert result["parse_failure_rate"] == {}  # no ok records -> no groups


def test_cost_sums_per_group_and_total():
    records = [_rec(cost_usd_est=0.01), _rec(cost_usd_est=0.02, condition="blocked_owned")]
    out = metrics.cost(records)
    assert out["lookup|anthropic|full"] == 0.01
    assert out["lookup|anthropic|blocked_owned"] == 0.02
    assert out["TOTAL"] == 0.03


def _company_field():
    return next(f for f in CANON["fields"] if f["id"] == "current_company")


def test_current_company_two_token_rule():
    f = _company_field()
    # the ordinary English word must NOT score as the company
    assert metrics.score_field(f, "I did X instead of Y") is False
    # proper noun + second token scores
    assert metrics.score_field(f, "Product Manager at Instead (instead.com)") is True
    assert metrics.score_field(f, "He is a PM at Instead working on tax filing.") is True
    # proper noun alone (no second signal) does not
    assert metrics.score_field(f, "Zoeb Nomi works at Instead.") is False
    # second token alone (no capitalised proper noun) does not
    assert metrics.score_field(f, "tax stuff, see instead.com") is False


def test_capitalised_token_is_case_sensitive_word_boundary():
    assert metrics._token_matches("Instead", "at Instead (") is True
    assert metrics._token_matches("Instead", "at Insteadly") is False   # \\b: whole word only
    assert metrics._token_matches("Instead", "at instead") is False     # case-sensitive
    assert metrics._token_matches("tax", "TAX planning") is True        # lowercase token: case-insensitive


def test_field_accuracy_scores_instead_of_as_zero_end_to_end():
    records = [_rec(answer_text="I did X instead of Y"),
               _rec(answer_text="Product Manager at Instead (instead.com)")]
    out = metrics.field_accuracy(records, CANON)
    assert out["current_company"]["by_group"]["lookup|anthropic|full"]["accuracy"] == 0.5


def test_groups_are_keyed_by_task_stack_condition():
    records = [_rec(task="lookup"), _rec(task="sourcing")]
    out = metrics.cost(records)
    assert out["lookup|anthropic|full"] == 0.01
    assert out["sourcing|anthropic|full"] == 0.01
