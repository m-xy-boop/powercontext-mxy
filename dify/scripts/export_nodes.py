"""Run in Dify API interpreter with normal DB environment; exports no secrets."""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
from app import app
from extensions.ext_database import db
from models.workflow import WorkflowNodeExecutionModel

ROOT = Path(__file__).resolve().parents[1]
report = json.loads((ROOT / "evidence/acceptance.json").read_text())
run_ids = [
    step["result"]["workflow_run_id"]
    for step in report["steps"]
    if "workflow_run_id" in step.get("result", {})
]
with app.app_context():
    nodes = (
        db.session.query(WorkflowNodeExecutionModel)
        .filter(WorkflowNodeExecutionModel.workflow_run_id.in_(run_ids))
        .order_by(WorkflowNodeExecutionModel.created_at)
        .all()
    )
    result = [
        {
            "workflow_run_id": node.workflow_run_id,
            "node_id": node.node_id,
            "node_type": node.node_type,
            "title": node.title,
            "status": node.status,
            "elapsed_time": node.elapsed_time,
            "error": node.error,
        }
        for node in nodes
    ]
    assert result
    (ROOT / "evidence/workflow-nodes.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    print("Exported", len(result), "actual Dify node executions")
