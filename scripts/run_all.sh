#!/usr/bin/env bash
set -euo pipefail
# End-to-end pipeline (T04 -> T09). Requires .env populated.
export $(grep -v '^#' .env | xargs) 2>/dev/null || true

python -m src.runner.generate_answers   --config config/config.yaml
python -m src.judge.run_judge           --config config/config.yaml
python -m src.metrics.deterministic     --config config/config.yaml
python -m src.metrics.aggregate         --config config/config.yaml
python -m src.metrics.agreement         --config config/config.yaml
python -m src.metrics.pass_fail         --config config/config.yaml
python -m src.discordance.export_review --config config/config.yaml
python -m src.report.build_report       --config config/config.yaml
echo "Done. See outputs/report/report.md"
