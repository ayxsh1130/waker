from sqlalchemy import text


def lock_experiment_resources(db):
    """Serialize scenario scheduling across API and controller PostgreSQL sessions."""
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(19042028)"))
