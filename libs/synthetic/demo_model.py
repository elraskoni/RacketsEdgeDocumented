"""Stand-in model for the demo prediction endpoint: Elo expectation from the Elo difference.

The production model is private. This keeps the endpoint's contract (predict_proba over the
same named feature columns) and uses the classic Elo formula, so demo probabilities are sensible.
"""
import numpy as np


class EloOnlyModel:
	def predict_proba(self, X):
		d = np.asarray(X["delta_elo_overall"], dtype="float64")
		p = 1.0 / (1.0 + 10.0 ** (-d / 400.0))
		return np.column_stack([1.0 - p, p])
