"""Universal document transformer.

A clean core that turns any supplier's Excel/PDF (invoice / packing / specification /
catalog, in any language) into one canonical per-article table, then renders the two
agreed output profiles.

Design goals (see documents/тз.txt):
- read the whole matrix uniformly for .xlsx / .xls / .pdf, including merged cells;
- map the many supplier columns onto a small fixed canonical set (column-level scoring,
  multilingual, not cell-by-cell guessing);
- merge rows by a normalized article key, handle blank-article continuation and the
  "numbered child + SOFA FABRIC family" layout, distribute group weights to children;
- never crash on a bad file: raise a friendly Russian error instead;
- the neural model improves/verifies the parse, it is not the source of truth.
"""
