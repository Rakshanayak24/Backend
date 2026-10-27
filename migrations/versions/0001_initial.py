"""Initial telemedicine core schema."""
from alembic import op
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Boolean, Enum, Text
from sqlalchemy import MetaData, Table
from app.models import Role, ConsultationStatus

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # Use the canonical ORM metadata to keep the initial schema aligned with models.
    from app.database import Base
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=op.get_bind())

def downgrade():
    from app.database import Base
    from app import models  # noqa: F401
    Base.metadata.drop_all(bind=op.get_bind())
