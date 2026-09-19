from sqlalchemy import create_engine

import app.db.models  # noqa: F401
from alembic import context
from app.core.config import settings
from app.db.base import Base

config = context.config
target_metadata = Base.metadata
url = settings().database_url
if context.is_offline_mode():
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url).connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
