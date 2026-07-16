"""Gold-set harness for the supply-chain extractor.

Two steps:
  1. `python -m nlp.gold.dump_candidates`  -> writes a model-assisted review file
     (retrieved passages + the union of every model variant's extractions).
  2. A human curates each line's `gold` list into the truth, saves it as
     `supply_gold.jsonl`, then `python -m nlp.gold.score` reports precision /
     recall / percentage accuracy per tier.

This turns the extractor from *monitored* (coverage check) into *evaluated*
(a real accuracy number) -- the yardstick every model swap needs.
"""
