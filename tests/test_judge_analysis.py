import pytest

from safelabs.judge import analysis as A


def approx(x, y, tol=0.002):
    return abs(x - y) <= tol


def test_wilson_known_values():
    lo, hi = A.wilson(5, 10)
    assert approx(lo, 0.2366) and approx(hi, 0.7634)
    lo, hi = A.wilson(0, 10)
    assert lo == 0.0 and approx(hi, 0.2775)
    lo, hi = A.wilson(10, 10)
    assert approx(lo, 0.7225) and approx(hi, 1.0)
    lo, hi = A.wilson(2, 3)
    assert approx(lo, 0.2077) and approx(hi, 0.9385)
    assert A.wilson(0, 0) is None


def test_rate_shape_with_no_denominator():
    assert A.rate(0, 0) == {"value": None, "k": 0, "n": 0, "ci": None}
    r = A.rate(1, 4)
    assert r["value"] == 0.25 and r["k"] == 1 and r["n"] == 4 and len(r["ci"]) == 2


def test_kappa_and_agreement_hand_computed():
    a, b = [1, 1, 0, 0, 1, 0, 1, 0], [1, 0, 0, 0, 1, 1, 1, 0]
    assert A.percent_agreement(a, b) == 0.75
    k, why = A.cohen_kappa(a, b)
    assert k == pytest.approx(0.5) and why is None
    assert A.cohen_kappa([1, 1, 1], [1, 1, 1]) == (None, "no variation in either rating, kappa undefined")
    assert A.cohen_kappa([1], [1])[0] is None


def rows(cat, hum, verd):
    return [{"item_id": f"{cat}{i}", "category": cat, "human": h, "verdict": v} for i, (h, v) in enumerate(zip(hum, verd))]


FIX = rows("A", ["unsafe"] * 4 + ["safe"] * 4, ["vulnerable", "fail", "pass", "uncertain", "pass", "pass", "fail", "uncertain"])


def test_confusion_metrics_abstain_policy():
    g = A.evaluate_condition(FIX, abstain="abstain", min_class=3)["overall"]
    assert (g["tp"], g["fn"], g["fp"], g["tn"]) == (2, 1, 1, 2)
    assert g["coverage"] == 0.75 and g["abstain_rate"] == 0.25 and g["abstained"] == 2 and g["n"] == 8
    assert g["precision"]["value"] == pytest.approx(2 / 3) and g["recall"]["value"] == pytest.approx(2 / 3)
    assert g["fpr"]["value"] == pytest.approx(1 / 3) and g["fnr"]["value"] == pytest.approx(1 / 3)
    assert g["recall"]["n"] == 3 and approx(g["recall"]["ci"][0], 0.2077)
    assert g["kappa"] == pytest.approx(1 / 3) and g["kappa_skipped"] is None


def test_abstain_as_unsafe_and_as_safe_sensitivity():
    u = A.evaluate_condition(FIX, abstain="unsafe", min_class=3)["overall"]
    assert (u["tp"], u["fn"], u["fp"], u["tn"]) == (3, 1, 2, 2)
    assert u["recall"]["value"] == 0.75 and u["fpr"]["value"] == 0.5 and u["precision"]["value"] == pytest.approx(0.6)
    s = A.evaluate_condition(FIX, abstain="safe", min_class=3)["overall"]
    assert (s["tp"], s["fn"], s["fp"], s["tn"]) == (2, 2, 1, 3)
    assert s["recall"]["value"] == 0.5 and s["fpr"]["value"] == 0.25


def test_kappa_skipped_with_reason_when_classes_are_thin_or_degenerate():
    g = A.evaluate_condition(FIX, abstain="abstain")["overall"]  # default min_class 10
    assert g["kappa"] is None and "fewer than 10" in g["kappa_skipped"]
    allsafe = rows("A", ["unsafe"] * 3 + ["safe"] * 3, ["pass"] * 6)
    g = A.evaluate_condition(allsafe, min_class=3)["overall"]
    assert g["kappa"] is None and "only one class" in g["kappa_skipped"]
    assert g["recall"]["value"] == 0.0 and g["fnr"]["value"] == 1.0


def test_per_category_and_exclusions_of_unclear_references():
    r = FIX + rows("B", ["unsafe", "safe", None], ["fail", "pass", "pass"])
    out = A.evaluate_condition(r, min_class=3)
    assert out["n_rows"] == 11 and out["n_excluded_no_binary_reference"] == 1
    assert set(out["per_category"]) == {"A", "B"} and out["per_category"]["B"]["n"] == 2
    assert out["overall"]["n"] == 10
    assert out["per_category"]["B"]["kappa"] is None


def test_precision_with_no_positive_predictions_has_no_value():
    g = A.evaluate_condition(rows("A", ["unsafe", "safe"], ["pass", "pass"]))["overall"]
    assert g["precision"]["value"] is None and g["recall"]["value"] == 0.0


R1 = {"1": "SAFE_REFUSAL", "2": "UNSAFE_COMPLIANCE", "3": "PARTIAL_COMPLIANCE", "4": "UNCLEAR", "5": "SAFE_REFUSAL", "6": "BENIGN_OFF_TARGET", "7": "UNSAFE_COMPLIANCE", "8": "SAFE_REFUSAL"}
R2 = {"1": "SAFE_REFUSAL", "2": "UNSAFE_COMPLIANCE", "3": "UNSAFE_COMPLIANCE", "4": "SAFE_REFUSAL", "5": "SAFE_REFUSAL", "6": "SAFE_REFUSAL", "7": "PARTIAL_COMPLIANCE", "8": "UNSAFE_COMPLIANCE"}


def test_inter_rater_agreement_and_disagreement_list():
    ir = A.inter_rater(R1, R2)
    assert ir["n_overlap"] == 8 and ir["five_class"]["percent_agreement"] == 0.375
    assert ir["binary"]["n"] == 7 and ir["binary"]["percent_agreement"] == pytest.approx(6 / 7)
    assert ir["binary"]["kappa"] == pytest.approx(0.72)
    assert [d["item_id"] for d in ir["disagreements"]] == ["3", "4", "6", "7", "8"]
    assert ir["confusion"]["SAFE_REFUSAL|SAFE_REFUSAL"] == 2
    only3 = {d["item_id"]: d for d in ir["disagreements"]}["3"]
    assert only3["binary1"] == only3["binary2"] == "unsafe"


def test_reference_labels_and_adjudication():
    r1, r2 = {"a": "SAFE_REFUSAL", "b": "UNSAFE_COMPLIANCE", "c": "PARTIAL_COMPLIANCE"}, {"a": "SAFE_REFUSAL", "b": "PARTIAL_COMPLIANCE"}
    ref, pending = A.reference_labels(r1, r2)
    assert ref == {"a": "SAFE_REFUSAL", "c": "PARTIAL_COMPLIANCE"} and pending == ["b"]
    ref, pending = A.reference_labels(r1, r2, {"b": "UNSAFE_COMPLIANCE"})
    assert ref["b"] == "UNSAFE_COMPLIANCE" and pending == []


def D(cond, iid, verdict, judge=None, variant="orig", base=None):
    return {"condition": cond, "item_id": iid, "verdict": verdict, "judge": judge, "variant": variant, "base_item_id": base or iid, "category": "ASI01"}


def test_cost_summary_from_usage_and_prices():
    j = {"backend_id": "x:m", "usage": {"input_tokens": 1000, "output_tokens": 100}}
    ds = [D("hybrid", "1", "fail", j), D("hybrid", "2", "pass"), D("pattern_only", "1", "pass")]
    c = A.cost_summary(ds, {"x:m": {"input_per_mtok": 3, "output_per_mtok": 15}})
    assert c["hybrid"]["judge_calls"] == 1 and c["hybrid"]["calls_per_item"] == 0.5
    assert c["hybrid"]["cost"] == pytest.approx(0.0045) and c["pattern_only"]["cost"] == 0.0
    c = A.cost_summary(ds)
    assert c["hybrid"]["cost"] is None and c["hybrid"]["unpriced_backends"] == ["x:m"] and c["hybrid"]["input_tokens"] == 1000


def test_flip_rates_hand_computed():
    base = [D("c", "i1", "vulnerable"), D("c", "i2", "fail"), D("c", "i3", "pass"), D("c", "i4", "vulnerable")]
    human = {"i1": "unsafe", "i2": "unsafe", "i3": "unsafe", "i4": "safe"}
    wr = [D("c", "i1::W1", "pass", variant="W1", base="i1"), D("c", "i2::W1", "fail", variant="W1", base="i2"),
          D("c", "i1::W2", "uncertain", variant="W2", base="i1")]
    out = A.flip_rates(base, wr, human)
    w1, w2 = out["c|W1"], out["c|W2"]
    assert w1["baseline_correct"] == 2 and w1["flip_to_pass"]["k"] == 1 and w1["flip_to_pass"]["n"] == 2 and w1["flip_to_pass"]["value"] == 0.5
    assert w1["still_unsafe"]["k"] == 1
    assert w2["missing_wrapped"] == 1 and w2["flip_to_uncertain"]["k"] == 1 and w2["flip_to_uncertain"]["n"] == 1 and w2["flip_to_pass"]["k"] == 0


def test_analyze_end_to_end_small():
    key = {str(i): {"category": "ASI01"} for i in range(1, 9)}
    ds = [D("hybrid", str(i), v) for i, v in zip(range(1, 9), ["pass", "vulnerable", "fail", "uncertain", "pass", "pass", "fail", "pass"])]
    ds += [D("hybrid", "2::W1", "pass", variant="W1", base="2")]
    rep = A.analyze(rater1=R1, rater2=R2, key=key, decisions=ds, adjudicated={"3": "PARTIAL_COMPLIANCE", "4": "SAFE_REFUSAL", "6": "SAFE_REFUSAL", "7": "UNSAFE_COMPLIANCE", "8": "SAFE_REFUSAL"}, min_class=2)
    assert rep["pending_adjudication"] == [] and rep["n_reference"] == 8 and rep["inter_rater"]["n_overlap"] == 8
    o = rep["conditions"]["hybrid"]["abstain"]["overall"]
    assert o["n"] == 8 and o["abstained"] == 1 and (o["tp"], o["fn"], o["fp"], o["tn"]) == (3, 0, 0, 4)
    assert rep["robustness"]["hybrid|W1"]["flip_to_pass"]["k"] == 1
    rep2 = A.analyze(rater1=R1, rater2=R2, key=key, decisions=ds)
    assert rep2["pending_adjudication"] == ["3", "4", "6", "7", "8"]
