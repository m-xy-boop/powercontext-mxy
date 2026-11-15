"""Run with the official Dify 1.9.2 API interpreter and cwd=its api directory.

Uses the same import/publish services as the console; does not implement a DSL runner.
The generated API credential is only written into ignored local runtime files.
"""

import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
from app import app
from extensions.ext_database import db
from models import Account
from models.model import ApiToken, App
from services.account_service import RegisterService
from services.app_dsl_service import AppDslService
from services.workflow_service import WorkflowService
from sqlalchemy.orm import Session
import yaml

root = Path(__file__).resolve().parents[1]
with app.app_context():
    account = db.session.query(Account).filter_by(email="judge@example.org").first()
    if account is None:
        RegisterService.setup(
            email="judge@example.org",
            name="回声当铺",
            password=secrets.token_urlsafe(24),
            ip_address="127.0.0.1",
            language="zh-Hans",
        )
        account = db.session.query(Account).filter_by(email="judge@example.org").one()
    from models.account import TenantAccountJoin

    join = db.session.query(TenantAccountJoin).filter_by(account_id=account.id).first()
    account.set_tenant_id(join.tenant_id)
    dsl = yaml.safe_load((root / "dsl/echo-pawnshop.yml").read_text())
    for var in dsl["workflow"]["environment_variables"]:
        if var["name"] == "ECHO_BASE_URL":
            var["value"] = os.getenv("ECHO_BASE_URL", "http://127.0.0.1:18080")
        elif var["name"] == "ECHO_TOOL_KEY":
            var["value"] = os.getenv("ECHO_TOOL_KEY", "local-demo-change-me")
    with Session(db.engine) as session:
        result = AppDslService(session).import_app(
            account=account,
            import_mode="yaml-content",
            yaml_content=yaml.safe_dump(dsl, allow_unicode=True, sort_keys=False),
        )
        session.commit()
        if result.status != "completed":
            raise RuntimeError(result.model_dump())
        model = session.get(App, result.app_id)
        workflow = WorkflowService().publish_workflow(
            session=session, app_model=model, account=account
        )
        session.flush()
        model.workflow_id = workflow.id
        credential = "app-" + secrets.token_hex(24)
        session.add(
            ApiToken(
                app_id=model.id, tenant_id=model.tenant_id, type="app", token=credential
            )
        )
        session.commit()
        report = {
            "dify_version": "1.9.2",
            "dsl_version": "0.4.0",
            "import": result.model_dump(),
            "published_workflow_id": workflow.id,
            "nodes": len(dsl["workflow"]["graph"]["nodes"]),
        }
    runtime = root / ".runtime"
    runtime.mkdir(exist_ok=True)
    target = runtime / "dify-credential.json"
    target.write_text(json.dumps({"api_key": credential, "app_id": result.app_id}))
    target.chmod(0o600)
    (root / "evidence").mkdir(exist_ok=True)
    (root / "evidence/import.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
