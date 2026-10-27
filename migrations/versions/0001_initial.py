"""Initial telemedicine core schema."""
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # Use the canonical ORM metadata to keep the initial schema aligned with models.
    from app import models  # noqa: F401
    from app.database import Base
    Base.metadata.create_all(bind=op.get_bind())

def downgrade():
    from app import models  # noqa: F401
    from app.database import Base
    Base.metadata.drop_all(bind=op.get_bind())
