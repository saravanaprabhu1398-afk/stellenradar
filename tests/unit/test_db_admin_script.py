import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import db_admin  # noqa: E402


class FakeLambda:
    def __init__(self, error=False):
        self.payloads = []
        self.error = error

    def invoke(self, FunctionName, Payload):  # noqa: N803
        payload = json.loads(Payload)
        self.payloads.append(payload)
        if self.error:
            body = {"errorType": "InvalidRequest", "errorMessage": "bad"}
            return {"FunctionError": "Unhandled", "Payload": io.BytesIO(json.dumps(body).encode())}
        body = {"mode": payload["mode"], "statements": 9, "applied": []}
        return {"Payload": io.BytesIO(json.dumps(body).encode())}


class FakeWaiter:
    def __init__(self, rds):
        self.rds = rds

    def wait(self, **kwargs):
        self.rds.waited = True


class FakeRds:
    def __init__(self, state):
        self.state, self.started, self.stopped, self.waited = state, False, False, False

    def describe_db_instances(self, DBInstanceIdentifier):  # noqa: N803
        return {"DBInstances": [{"DBInstanceStatus": self.state,
                                 "MasterUserSecret": {"SecretArn": "arn:secret"}}]}  # fmt: skip

    def start_db_instance(self, DBInstanceIdentifier):  # noqa: N803
        self.started = True

    def stop_db_instance(self, DBInstanceIdentifier):  # noqa: N803
        self.stopped = True

    def get_waiter(self, name):
        return FakeWaiter(self)


class FakeSecrets:
    def get_secret_value(self, SecretId):  # noqa: N803
        assert SecretId == "arn:secret"
        return {"SecretString": json.dumps({"username": "stellenradar_admin", "password": "pw"})}


def test_bootstrap_passes_master_password_then_migrates(capsys):
    lam = FakeLambda()
    assert db_admin.main(["bootstrap"], lam=lam, rds=FakeRds("available"), secrets=FakeSecrets()) == 0
    assert lam.payloads == [{"mode": "bootstrap", "master_password": "pw"}, {"mode": "migrate"}]
    assert "pw" not in capsys.readouterr().out


def test_tracker_add_and_update_payloads():
    lam = FakeLambda()
    db_admin.main(["tracker-add", "--company", "ACME", "--role", "Data Engineer", "--date", "2026-11-09"], lam=lam)
    assert lam.payloads[-1] == {"mode": "tracker_upsert", "application": {
        "company": "ACME", "role_title": "Data Engineer", "applied_date": "2026-11-09"}}  # fmt: skip
    db_admin.main(["tracker-update", "7", "--status", "interview"], lam=lam)
    assert lam.payloads[-1] == {"mode": "tracker_upsert", "application": {"application_id": 7, "status": "interview"}}
    db_admin.main(["tracker-list"], lam=lam)
    assert lam.payloads[-1] == {"mode": "tracker_list"}


def test_function_error_exits_with_message():
    with pytest.raises(SystemExit, match="InvalidRequest: bad"):
        db_admin.main(["status"], lam=FakeLambda(error=True))


def test_up_starts_and_waits_down_stops():
    stopped = FakeRds("stopped")
    db_admin.main(["up"], rds=stopped)
    assert stopped.started
    assert stopped.waited
    running = FakeRds("available")
    db_admin.main(["up"], rds=running)
    assert not running.started
    db_admin.main(["down"], rds=running)
    assert running.stopped
    assert db_admin.down(FakeRds("stopped")) == "stopped"
