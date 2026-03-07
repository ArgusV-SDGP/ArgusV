"""tests/test_api.py — Task TEST-05"""
import pytest
from fastapi.testclient import TestClient

# TODO TEST-05: mock DB + bus so API tests don't need real Postgres
# from api.server import app
# client = TestClient(app)
#
# def test_health():
#     r = client.get("/health")
#     assert r.status_code == 200
#     assert r.json()["status"] == "healthy"
#
# def test_list_cameras_empty():
#     r = client.get("/api/cameras")
#     assert r.status_code == 200
#     assert isinstance(r.json(), list)
#
# def test_list_incidents_empty():
#     r = client.get("/api/incidents")
#     assert r.status_code == 200
