import base64
import hashlib
import hmac
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import Text, cast, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base, SessionLocal, engine, get_db
from app.models import (
    AuditLog,
    AvailabilitySlot,
    Consultation,
    ConsultationStatus,
    Doctor,
    IdempotencyRecord,
    OutboxEvent,
    Prescription,
    Profile,
    Role,
    User,
)
from app.schemas import (
    BookingIn,
    ConsultationOut,
    DoctorCreate,
    LoginIn,
    PrescriptionIn,
    RegisterIn,
    SlotCreate,
    SlotOut,
    StatusUpdate,
    TokenOut,
    UserOut,
)
from app.security import (
    create_access_token,
    current_user,
    hash_password,
    require_roles,
    verify_password,
)

logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("amrutam")
_rate_counts: dict[str, tuple[int, int]] = {}
_redis = Redis.from_url(settings.redis_url, socket_connect_timeout=0.15, socket_timeout=0.15,
                        decode_responses=True)
REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "path", "status"])
LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency", ["method", "path"])


def audit(db: Session, actor: str | None, action: str, resource_type: str,
          resource_id: str | None = None, details: dict | None = None, ip: str | None = None):
    db.add(AuditLog(actor_id=actor, action=action, resource_type=resource_type,
                    resource_id=resource_id, details=details or {}, ip_address=ip))


def enqueue(db: Session, event_type: str, payload: dict):
    # Keep only identifiers and event metadata here; consumers load authorized data as needed.
    db.add(OutboxEvent(event_type=event_type, payload=payload))


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.environment.lower() in {"production", "prod"}:
        if len(settings.jwt_secret) < 32 or settings.jwt_secret.startswith("development-"):
            raise RuntimeError("Production requires a strong JWT_SECRET of at least 32 characters")
        if settings.database_url.startswith("sqlite"):
            raise RuntimeError("Production requires PostgreSQL")
    Base.metadata.create_all(bind=engine)  # Alembic owns production schema evolution; this keeps local startup simple.
    if settings.bootstrap_admin_email and settings.bootstrap_admin_password:
        with SessionLocal() as db:
            if not db.scalar(select(User).where(User.email == settings.bootstrap_admin_email.lower())):
                admin = User(email=settings.bootstrap_admin_email.lower(),
                             password_hash=hash_password(settings.bootstrap_admin_password),
                             full_name="System administrator", role=Role.admin)
                db.add(admin)
                db.commit()
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", description="Telemedicine workflows API",
              lifespan=lifespan, docs_url="/docs", redoc_url="/redoc")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
                   allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])

if settings.otel_exporter_otlp_endpoint:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": "amrutam-care-api"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument(engine=engine)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))[:128]
    start = time.perf_counter()
    if request.url.path not in ("/health/live", "/health/ready", "/metrics"):
        client_ip = request.client.host if request.client else "unknown"
        minute = int(time.time() // 60)
        key = f"rate:{client_ip}:{minute}"
        try:
            count = _redis.incr(key)
            if count == 1:
                _redis.expire(key, 120)
        except RedisError:
            # Local development fallback only. Production deployments should keep Redis available.
            if len(_rate_counts) > 10000:
                _rate_counts.clear()
            count, expiry = _rate_counts.get(client_ip, (0, minute))
            if expiry != minute:
                count = 0
            count += 1
            _rate_counts[client_ip] = (count, minute)
        if count > settings.rate_limit_per_minute:
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"},
                                headers={"Retry-After": str(60 - int(time.time() % 60))})
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    path = request.url.path
    REQUESTS.labels(request.method, path, str(response.status_code)).inc()
    LATENCY.labels(request.method, path).observe(elapsed)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    logger.info("request_complete", extra={"request_id": request_id, "method": request.method,
                                           "path": path, "status": response.status_code,
                                           "duration_ms": round(elapsed * 1000, 2)})
    return response


@app.exception_handler(IntegrityError)
def integrity_error_handler(_: Request, exc: IntegrityError):
    logger.warning("integrity_conflict", extra={"error_type": type(exc).__name__})
    return JSONResponse(status_code=409, content={"detail": "Resource conflicts with an existing record"})


@app.get("/health/live", tags=["operations"])
def live():
    return {"status": "live"}


@app.get("/health/ready", tags=["operations"])
def ready(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/auth/register", response_model=UserOut, status_code=201, tags=["auth"])
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)):
    user = User(email=body.email.lower(), password_hash=hash_password(body.password),
                full_name=body.full_name, phone=body.phone, role=Role.patient)
    db.add(user)
    db.flush()
    db.add(Profile(user_id=user.id))
    audit(db, user.id, "user.register", "user", user.id, ip=request.client.host if request.client else None)
    db.commit()
    db.refresh(user)
    return user


@app.post("/v1/auth/login", response_model=TokenOut, tags=["auth"])
def login(body: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if not user or not verify_password(body.password, user.password_hash) or not user.is_active:
        raise HTTPException(401, "Email or password is incorrect", headers={"WWW-Authenticate": "Bearer"})
    if user.mfa_enabled and (not body.mfa_code or not verify_totp(user, body.mfa_code)):
        raise HTTPException(401, "A valid MFA code is required")
    token, seconds = create_access_token(user)
    audit(db, user.id, "auth.login", "user", user.id)
    db.commit()
    return TokenOut(access_token=token, expires_in=seconds)


def _fernet() -> Fernet:
    if not settings.mfa_encryption_key:
        raise HTTPException(503, "MFA is not configured")
    try:
        return Fernet(settings.mfa_encryption_key.encode())
    except ValueError:
        raise HTTPException(503, "MFA encryption key is invalid")


def verify_totp(user: User, code: str) -> bool:
    if not user.mfa_secret_encrypted:
        return False
    try:
        secret = _fernet().decrypt(user.mfa_secret_encrypted.encode()).decode()
    except (InvalidToken, UnicodeDecodeError):
        return False
    counter = int(time.time()) // 30
    for drift in (-1, 0, 1):
        digest = hmac.new(base64.b32decode(secret), (counter + drift).to_bytes(8, "big"), hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        number = (int.from_bytes(digest[offset:offset + 4], "big") & 0x7FFFFFFF) % 1_000_000
        if hmac.compare_digest(f"{number:06d}", code):
            return True
    return False


@app.post("/v1/auth/mfa/setup", tags=["auth"])
def setup_mfa(user: User = Depends(current_user), db: Session = Depends(get_db)):
    secret = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    encrypted = _fernet().encrypt(secret.encode()).decode()
    user.mfa_secret_encrypted = encrypted
    user.mfa_enabled = False
    db.commit()
    # The shared secret is returned once; clients should immediately enroll it in an authenticator.
    uri = f"otpauth://totp/Amrutam:{user.email}?secret={secret}&issuer=Amrutam&algorithm=SHA1&digits=6&period=30"
    return {"secret": secret, "otpauth_uri": uri, "confirmation_required": True}


@app.post("/v1/auth/mfa/confirm", tags=["auth"])
def confirm_mfa(code: str = Query(pattern=r"^\d{6}$"), user: User = Depends(current_user), db: Session = Depends(get_db)):
    if not verify_totp(user, code):
        raise HTTPException(422, "MFA code is invalid")
    user.mfa_enabled = True
    audit(db, user.id, "auth.mfa_enabled", "user", user.id)
    db.commit()
    return {"mfa_enabled": True}


@app.get("/v1/users/me", response_model=UserOut, tags=["users"])
def me(user: User = Depends(current_user)):
    return user


@app.post("/v1/doctors", status_code=201, tags=["doctors"])
def create_doctor(body: DoctorCreate, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role not in (Role.doctor, Role.admin):
        raise HTTPException(403, "Doctor role required")
    if db.scalar(select(Doctor).where(Doctor.user_id == user.id)):
        raise HTTPException(409, "Doctor profile already exists")
    doctor = Doctor(user_id=user.id, specialty=body.specialty, bio=body.bio,
                    languages=body.languages, consultation_fee_minor=body.consultation_fee_minor,
                    verified=(user.role == Role.admin))
    db.add(doctor)
    db.flush()
    audit(db, user.id, "doctor.create", "doctor", doctor.id, ip=request.client.host if request.client else None)
    db.commit()
    return {"id": doctor.id, "user_id": doctor.user_id, "specialty": doctor.specialty, "verified": doctor.verified}


@app.post("/v1/doctors/{doctor_id}/availability", response_model=SlotOut, status_code=201, tags=["availability"])
def add_slot(doctor_id: str, body: SlotCreate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    doctor = db.get(Doctor, doctor_id)
    if not doctor or (doctor.user_id != user.id and user.role != Role.admin):
        raise HTTPException(404, "Doctor not found")
    if not doctor.verified:
        raise HTTPException(403, "Doctor profile must be verified")
    if body.starts_at <= __import__("datetime").datetime.now(__import__("datetime").timezone.utc):
        raise HTTPException(422, "Availability must be in the future")
    slot = AvailabilitySlot(doctor_id=doctor_id, starts_at=body.starts_at, ends_at=body.ends_at)
    db.add(slot)
    audit(db, user.id, "availability.create", "availability_slot", slot.id)
    db.commit()
    db.refresh(slot)
    return slot


@app.get("/v1/doctors", tags=["doctors"])
def search_doctors(q: str | None = Query(default=None, max_length=100), specialty: str | None = Query(default=None, max_length=100),
                   language: str | None = Query(default=None, max_length=50), limit: int = Query(default=20, ge=1, le=100),
                   offset: int = Query(default=0, ge=0), db: Session = Depends(get_db)):
    query = select(Doctor).where(Doctor.verified.is_(True))
    if q:
        query = query.where(Doctor.bio.ilike(f"%{q}%"))
    if specialty:
        query = query.where(Doctor.specialty.ilike(f"%{specialty}%"))
    if language:
        query = query.where(cast(Doctor.languages, Text).ilike(f'%"{language}"%'))
    rows = db.scalars(query.order_by(Doctor.created_at.desc()).offset(offset).limit(limit)).all()
    return [{"id": d.id, "user_id": d.user_id, "specialty": d.specialty, "bio": d.bio,
             "languages": d.languages, "consultation_fee_minor": d.consultation_fee_minor} for d in rows]


@app.get("/v1/doctors/{doctor_id}/availability", response_model=list[SlotOut], tags=["availability"])
def list_slots(doctor_id: str, db: Session = Depends(get_db)):
    return db.scalars(select(AvailabilitySlot).where(AvailabilitySlot.doctor_id == doctor_id,
                      AvailabilitySlot.is_booked.is_(False)).order_by(AvailabilitySlot.starts_at).limit(100)).all()


@app.post("/v1/consultations", response_model=ConsultationOut, status_code=201, tags=["consultations"])
def book(body: BookingIn, request: Request, idempotency_key: str = Header(min_length=8, max_length=128, alias="Idempotency-Key"),
         user: User = Depends(require_roles(Role.patient, Role.admin)), db: Session = Depends(get_db)):
    existing = db.get(IdempotencyRecord, {"key": idempotency_key, "user_id": user.id})
    if existing:
        return db.get(Consultation, existing.resource_id)
    doctor = db.get(Doctor, body.doctor_id)
    if not doctor or not doctor.verified:
        raise HTTPException(404, "Verified doctor not found")
    # FOR UPDATE serializes competing PostgreSQL bookings; unique constraints provide a final race guard.
    slot = db.scalar(select(AvailabilitySlot).where(AvailabilitySlot.id == body.slot_id,
                      AvailabilitySlot.doctor_id == body.doctor_id, AvailabilitySlot.is_booked.is_(False)).with_for_update())
    if not slot:
        raise HTTPException(409, "Availability slot is no longer available")
    slot.is_booked = True
    consultation = Consultation(doctor_id=body.doctor_id, patient_id=user.id, slot_id=slot.id, reason=body.reason)
    db.add(consultation)
    db.flush()
    db.add(IdempotencyRecord(key=idempotency_key, user_id=user.id, resource_id=consultation.id))
    audit(db, user.id, "consultation.book", "consultation", consultation.id,
          {"doctor_id": body.doctor_id, "slot_id": slot.id}, request.client.host if request.client else None)
    enqueue(db, "consultation.booked", {"consultation_id": consultation.id})
    db.commit()
    db.refresh(consultation)
    return consultation


@app.get("/v1/consultations", response_model=list[ConsultationOut], tags=["consultations"])
def my_consultations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    query = select(Consultation).order_by(Consultation.created_at.desc()).limit(100)
    if user.role == Role.doctor:
        doctor = db.scalar(select(Doctor).where(Doctor.user_id == user.id))
        if not doctor:
            return []
        query = query.where(Consultation.doctor_id == doctor.id)
    elif user.role == Role.patient:
        query = query.where(Consultation.patient_id == user.id)
    return db.scalars(query).all()


@app.patch("/v1/consultations/{consultation_id}", response_model=ConsultationOut, tags=["consultations"])
def update_consultation(consultation_id: str, body: StatusUpdate, request: Request,
                        user: User = Depends(current_user), db: Session = Depends(get_db)):
    consultation = db.get(Consultation, consultation_id)
    if not consultation:
        raise HTTPException(404, "Consultation not found")
    doctor = db.get(Doctor, consultation.doctor_id)
    allowed = user.role == Role.admin or (user.role == Role.doctor and doctor.user_id == user.id) or (
        user.role == Role.patient and consultation.patient_id == user.id and body.status == ConsultationStatus.cancelled)
    if not allowed:
        raise HTTPException(403, "Not permitted to update this consultation")
    transitions = {ConsultationStatus.booked: {ConsultationStatus.confirmed, ConsultationStatus.cancelled},
                   ConsultationStatus.confirmed: {ConsultationStatus.in_progress, ConsultationStatus.cancelled},
                   ConsultationStatus.in_progress: {ConsultationStatus.completed},
                   ConsultationStatus.completed: set(), ConsultationStatus.cancelled: set()}
    if body.status not in transitions[consultation.status]:
        raise HTTPException(409, f"Invalid transition from {consultation.status.value} to {body.status.value}")
    old = consultation.status
    consultation.status = body.status
    if body.notes is not None and user.role in (Role.doctor, Role.admin):
        consultation.notes = body.notes
    if body.status == ConsultationStatus.cancelled:
        slot = db.get(AvailabilitySlot, consultation.slot_id)
        if slot:
            slot.is_booked = False
    audit(db, user.id, "consultation.status_change", "consultation", consultation.id,
          {"from": old.value, "to": body.status.value}, request.client.host if request.client else None)
    enqueue(db, f"consultation.{body.status.value}", {"consultation_id": consultation.id})
    db.commit()
    db.refresh(consultation)
    return consultation


@app.post("/v1/consultations/{consultation_id}/prescriptions", status_code=201, tags=["prescriptions"])
def prescribe(consultation_id: str, body: PrescriptionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    consultation = db.get(Consultation, consultation_id)
    if not consultation or consultation.status != ConsultationStatus.completed:
        raise HTTPException(404, "Completed consultation not found")
    doctor = db.get(Doctor, consultation.doctor_id)
    if user.role != Role.admin and (user.role != Role.doctor or not doctor or doctor.user_id != user.id):
        raise HTTPException(403, "Treating doctor role required")
    prescription = Prescription(consultation_id=consultation.id, doctor_id=consultation.doctor_id,
                                patient_id=consultation.patient_id, items=body.items, instructions=body.instructions)
    db.add(prescription)
    db.flush()
    audit(db, user.id, "prescription.create", "prescription", prescription.id)
    enqueue(db, "prescription.created", {"prescription_id": prescription.id})
    db.commit()
    return {"id": prescription.id, "consultation_id": prescription.consultation_id,
            "items": prescription.items, "instructions": prescription.instructions}


@app.get("/v1/consultations/{consultation_id}/prescriptions", tags=["prescriptions"])
def get_prescriptions(consultation_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    consultation = db.get(Consultation, consultation_id)
    if not consultation:
        raise HTTPException(404, "Consultation not found")
    doctor = db.get(Doctor, consultation.doctor_id)
    if user.role != Role.admin and user.id not in (consultation.patient_id, doctor.user_id if doctor else None):
        raise HTTPException(403, "Not permitted to view these prescriptions")
    rows = db.scalars(select(Prescription).where(Prescription.consultation_id == consultation_id)).all()
    audit(db, user.id, "prescription.read", "consultation", consultation_id)
    db.commit()
    return rows


@app.get("/v1/admin/analytics", tags=["admin"])
def analytics(user: User = Depends(require_roles(Role.admin)), db: Session = Depends(get_db)):
    counts = {"users": db.scalar(select(func.count()).select_from(User)) or 0,
              "doctors": db.scalar(select(func.count()).select_from(Doctor)) or 0,
              "consultations": db.scalar(select(func.count()).select_from(Consultation)) or 0}
    counts["consultations_by_status"] = {s.value: db.scalar(select(func.count()).select_from(Consultation).where(Consultation.status == s)) or 0
                                         for s in ConsultationStatus}
    return counts


@app.patch("/v1/admin/users/{user_id}/role", tags=["admin"])
def change_role(user_id: str, role: Role, request: Request,
                user: User = Depends(require_roles(Role.admin)), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    previous = target.role
    target.role = role
    audit(db, user.id, "user.role_change", "user", target.id,
          {"from": previous.value, "to": role.value}, request.client.host if request.client else None)
    db.commit()
    return {"id": target.id, "role": target.role.value}


@app.patch("/v1/admin/doctors/{doctor_id}/verify", tags=["admin"])
def verify_doctor(doctor_id: str, request: Request, verified: bool = Query(),
                  user: User = Depends(require_roles(Role.admin)), db: Session = Depends(get_db)):
    doctor = db.get(Doctor, doctor_id)
    if not doctor:
        raise HTTPException(404, "Doctor profile not found")
    doctor.verified = verified
    audit(db, user.id, "doctor.verification_change", "doctor", doctor.id, {"verified": verified},
          request.client.host if request.client else None)
    db.commit()
    return {"id": doctor.id, "verified": doctor.verified}


def export_openapi():
    import json
    with open("openapi.json", "w", encoding="utf-8") as output:
        json.dump(app.openapi(), output, indent=2)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000)
