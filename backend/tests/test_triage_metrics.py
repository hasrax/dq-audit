from app.eval.triage_metrics import compute_triage_metrics


def _issue(source, status):
    return {"source": source, "status": status}


def test_empty_gives_none_metrics():
    m = compute_triage_metrics([])
    assert m["total"] == 0
    assert m["empirical_precision"] is None
    assert m["hallucination_rate"] is None


def test_empirical_precision_per_source():
    issues = [
        _issue("rule", "accepted"), _issue("rule", "accepted"), _issue("rule", "rejected"),
        _issue("llm", "accepted"), _issue("llm", "rejected"),
    ]
    m = compute_triage_metrics(issues)
    assert m["by_source"]["rule"]["empirical_precision"] == round(2 / 3, 4)
    assert m["by_source"]["llm"]["empirical_precision"] == 0.5
    assert m["empirical_precision"] == round(3 / 5, 4)  # combined


def test_flagged_issues_excluded_from_precision():
    issues = [_issue("rule", "accepted"), _issue("rule", "flagged"), _issue("rule", "flagged")]
    m = compute_triage_metrics(issues)
    # only the decided one counts -> precision 1.0, but reviewed = 1 of 3
    assert m["by_source"]["rule"]["empirical_precision"] == 1.0
    assert m["reviewed"] == 1


def test_hallucination_rate_is_rejected_llm_over_total_llm():
    issues = [
        _issue("llm", "accepted"), _issue("llm", "rejected"),
        _issue("llm", "rejected"), _issue("llm", "flagged"),
    ]
    m = compute_triage_metrics(issues)
    assert m["hallucination_rate"] == 0.5  # 2 rejected of 4 llm


def test_ambiguity_rate_is_considered_over_total():
    issues = [_issue("rule", "considered"), _issue("rule", "accepted"), _issue("llm", "flagged"), _issue("llm", "considered")]
    m = compute_triage_metrics(issues)
    assert m["ambiguity_rate"] == 0.5  # 2 considered of 4
