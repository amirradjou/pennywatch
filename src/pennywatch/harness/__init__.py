"""Code that runs inside the measuring container, under Valgrind.

Every line here is part of the instrument: editing it can move the counts, so results record a hash
of this directory and comparisons warn when it differs. Only `expected` is imported on the host, so
it must stay free of PennyLane.
"""
