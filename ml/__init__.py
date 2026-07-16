"""Phase 5 -- the ML spine: an 8-K 'substance' classifier, batch-trained.

Predicts whether an 8-K reports a *material* corporate event (a real news
signal) or a *routine* procedural filing, from the filing TEXT alone. The label
is derived from the 8-K Item codes (the submissions-API `items` field), which are
deliberately kept OUT of the feature set -- so the model learns materiality from
language, not by memorising the code that defines the label.

Lifecycle (all batch, all Dagster assets in Phase 3's graph):
    items  -> label layer (Item codes -> material/routine)   ml.items
    train  -> TF-IDF + LogReg, logged & registered in MLflow  ml.model.train
    eval   -> score a recent slice, append metric time series ml.model.evaluate
    drift  -> PSI of the score distribution, an asset-check    ml.model.drift
"""
