"""
Write the stand-in win-probability model for the demo (/v1/tennis/predict).

	python scripts/train_demo_model.py --out ./library/models/demo_model.joblib

The production model and its training pipeline are private. The demo model applies the Elo
expectation to the Elo difference the endpoint computes (libs/synthetic/demo_model.py), with
the same artifact format: {"model": ..., "metadata": {"feature_names": [...]}}.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import joblib  # noqa: E402

from libs.synthetic.demo_model import EloOnlyModel  # noqa: E402
from services.api import predict  # noqa: E402


def main() -> int:
	ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
	ap.add_argument("--out", default="./library/models/demo_model.joblib")
	args = ap.parse_args()
	names = ([f"delta_career_{k}" for k in predict._CAREER_KEYS] + [f"delta_recent5_{k}" for k in predict._RECENT_KEYS]
		+ ["delta_elo_overall", "side_home", "side_away", "side_unknown", "surface_unknown"])
	os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
	joblib.dump({"model": EloOnlyModel(), "metadata": {"feature_names": names}}, args.out)
	print(f"[model] wrote {args.out}")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
