from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import AvailabilitySlot, Doctor, Role, User
from app.security import hash_password


@pytest.fixture
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(engine)
    def override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        test_client.state.session_factory = TestingSession
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


def register_and_login(client, email="patient@example.com"):
    registered = client.post("/v1/auth/register", json={"email": email, "password": "a-very-long-test-password",
                                                        "full_name": "Test Patient"})
    assert registered.status_code == 201
    logged = client.post("/v1/auth/login", json={"email": email, "password": "a-very-long-test-password"})
    assert logged.status_code == 200
    return {"Authorization": f"Bearer {logged.json()['access_token']}"}


def test_registration_login_and_authenticated_profile(client):
    headers = register_and_login(client)
    profile = client.get("/v1/users/me", headers=headers)
    assert profile.status_code == 200
    assert profile.json()["role"] == "patient"


def test_invalid_login_does_not_disclose_account_state(client):
    register_and_login(client)
    response = client.post("/v1/auth/login", json={"email": "patient@example.com", "password": "wrong"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Email or password is incorrect"


def test_booking_requires_idempotency_key_and_patient_auth(client):
    headers = register_and_login(client)
    response = client.post("/v1/consultations", headers=headers, json={"doctor_id": "missing", "slot_id": "missing"})
    assert response.status_code == 422


def test_booking_is_idempotent_for_same_user_and_key(client):
    headers = register_and_login(client)
    session_factory = client.state.session_factory
    with session_factory.begin() as db:
        doctor_user = User(email="doctor@example.com", password_hash=hash_password("another-long-test-password"),
                           full_name="Test Doctor", role=Role.doctor)
        db.add(doctor_user)
        db.flush()
        doctor = Doctor(user_id=doctor_user.id, specialty="Family medicine", verified=True)
        db.add(doctor)
        db.flush()
        slot = AvailabilitySlot(doctor_id=doctor.id, starts_at=datetime.now(timezone.utc) + timedelta(days=1),
                                ends_at=datetime.now(timezone.utc) + timedelta(days=1, minutes=30))
        db.add(slot)
        db.flush()
        doctor_id, slot_id = doctor.id, slot.id
    body = {"doctor_id": doctor_id, "slot_id": slot_id, "reason": "Annual checkup"}
    first = client.post("/v1/consultations", headers={**headers, "Idempotency-Key": "patient-booking-0001"}, json=body)
    second = client.post("/v1/consultations", headers={**headers, "Idempotency-Key": "patient-booking-0001"}, json=body)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_health_readiness(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
