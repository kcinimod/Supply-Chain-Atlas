"""nlp/extract.py post-filter (LLM call stubbed out) + nlp/resolve.py matching."""
from __future__ import annotations

from nlp import extract, ollama_client, resolve


# --- extract._is_real_name -----------------------------------------------------
def test_is_real_name_accepts_real_companies():
    assert extract._is_real_name("NVIDIA", True)
    assert extract._is_real_name("Applied Materials", True)
    assert extract._is_real_name("Amgen", True)     # 'a...' prefix needs \b to demote


def test_is_real_name_demotes_placeholders():
    for placeholder in ("Customer A", "customer b", "one customer",
                        "our largest customer", "Two customers", "a customer",
                        "certain distributors", "several clients", "unnamed"):
        assert not extract._is_real_name(placeholder, True), placeholder


def test_is_real_name_respects_model_flag_and_empty():
    assert not extract._is_real_name("NVIDIA", False)
    assert not extract._is_real_name("", True)


def test_is_real_name_keeps_the_prefixed_legal_names():
    # Legal names starting with 'The' are real companies, not placeholders.
    assert extract._is_real_name("The Boeing Company", True)


def test_is_real_name_demotes_the_generic_phrases():
    assert not extract._is_real_name("the customer", True)
    assert not extract._is_real_name("The U.S. Government", True)


# --- extract.extract_facts (Ollama stubbed) --------------------------------------
def _stub_chat(monkeypatch, customers):
    calls = []

    def fake_chat_json(prompt, schema, **kw):
        calls.append(prompt)
        return {"customers": customers}

    monkeypatch.setattr(ollama_client, "chat_json", fake_chat_json)
    return calls


def test_extract_facts_empty_passages_never_calls_model(monkeypatch):
    calls = _stub_chat(monkeypatch, [])
    assert extract.extract_facts([]) == []
    assert calls == []


def test_extract_facts_post_filter(monkeypatch):
    _stub_chat(monkeypatch, [
        {"name": "Applied Materials", "pct_of_revenue": 32, "named": True,
         "quote": "Applied Materials accounted for 32% of revenue"},
        {"name": "Customer A", "pct_of_revenue": 15, "named": True,   # placeholder
         "quote": "Customer A represented 15% of net sales"},
        {"name": "", "pct_of_revenue": 9, "named": False, "quote": ""},  # dropped
        {"name": "applied materials", "pct_of_revenue": 32, "named": True,
         "quote": "duplicate"},                                          # deduped
        {"name": "Lam Research", "pct_of_revenue": None, "named": True,
         "quote": "q" * 1000},                                           # truncated
    ])

    facts = extract.extract_facts(["some passage"])

    assert [f["name"] for f in facts] == ["Applied Materials", "unnamed", "Lam Research"]
    amat, anon, lam = facts
    assert amat == {"name": "Applied Materials", "pct_of_revenue": 32,
                    "is_named": True,
                    "quote": "Applied Materials accounted for 32% of revenue"}
    assert anon["is_named"] is False and anon["pct_of_revenue"] == 15
    assert lam["pct_of_revenue"] is None
    assert len(lam["quote"]) == 600


# --- resolve.normalize ------------------------------------------------------------
def test_normalize_strips_suffixes_and_punctuation():
    assert resolve.normalize("NVIDIA Corporation") == "nvidia"
    assert resolve.normalize("Advanced Micro Devices, Inc.") == "advanced micro devices"
    assert resolve.normalize("Lattice Semiconductor Corp") == "lattice"
    assert resolve.normalize("The Boeing Company") == "boeing"
    assert resolve.normalize("Inc.") == ""
    assert resolve.normalize("  ") == ""


# --- resolve.Resolver ---------------------------------------------------------------
COMPANIES = [
    (1045810, "NVDA", "NVIDIA Corp", True),
    (2488, "AMD", "Advanced Micro Devices, Inc.", True),
    (777, "ENTG", "Entegris, Inc.", True),
    (1297996, "DLR", "Digital Realty Trust, Inc.", True),
    (9999, "OOU", "Out Of Universe Corp", False),      # excluded from matching
]


def test_resolver_alias_match():
    r = resolve.Resolver(COMPANIES)
    # 'NVIDIA' normalizes to the alias key -> ticker -> CIK.
    assert r.resolve("NVIDIA") == 1045810
    assert r.resolve("Advanced Micro Devices") == 2488   # alias, full prose name
    assert r.resolve("AMD") == 2488


def test_resolver_exact_normalized_match():
    r = resolve.Resolver(COMPANIES)
    assert r.resolve("Entegris") == 777
    assert r.resolve("Entegris, Inc.") == 777


def test_resolver_leading_token_match():
    r = resolve.Resolver(COMPANIES)
    # 'digital' is the first token of normalized 'digital realty trust'.
    assert r.resolve("Digital") == 1297996
    # Heads of <= 2 chars never leading-token match.
    assert r.resolve("Di") is None


def test_resolver_misses():
    r = resolve.Resolver(COMPANIES)
    assert r.resolve("Totally Unknown Industries") is None
    assert r.resolve("Out Of Universe Corp") is None     # in dim but not in-universe
    assert r.resolve("Inc.") is None                     # normalizes to empty
    # Alias exists but its ticker is not in the (in-universe) set.
    assert r.resolve("Tesla") is None


# --- pct-in-quote fabrication guard (v2 Phase 8) ---------------------------------
def test_grounded_pct_kept_when_number_in_quote():
    assert extract._grounded_pct(14, "Customer X accounted for 14% of revenue") == 14
    assert extract._grounded_pct(10.5, "approximately 10.5% of net revenue") == 10.5


def test_grounded_pct_dropped_when_fabricated():
    # filing says "more than 10%" but the model invented 58.7 -> drop the number
    assert extract._grounded_pct(58.7, "one customer accounted for more than 10% of revenue") is None


def test_grounded_pct_handles_missing_inputs():
    assert extract._grounded_pct(None, "anything") is None
    assert extract._grounded_pct(12, "") is None
    assert extract._grounded_pct("not-a-number", "12%") is None


def test_extract_facts_grounds_pct(monkeypatch):
    _stub_chat(monkeypatch, [
        {"name": "NVIDIA", "pct_of_revenue": 58.7, "named": True,
         "quote": "one customer accounted for more than 10% of our revenue"},
        {"name": "Applied Materials", "pct_of_revenue": 21.0, "named": True,
         "quote": "Applied Materials represented 21% of net sales"},
    ])
    facts = extract.extract_facts(["passage"])
    by_name = {f["name"]: f for f in facts}
    assert by_name["NVIDIA"]["pct_of_revenue"] is None          # fabricated -> dropped
    assert by_name["Applied Materials"]["pct_of_revenue"] == 21.0  # grounded -> kept
