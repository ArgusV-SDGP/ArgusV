# ArgusV Database Migrations

We use **Alembic** to manage the PostgreSQL schema.

### 1. Create Initial Migration (Auto-generate)
Run this inside the `decision-engine` container (or locally if you have the env vars set).

```bash
# Inside the container
cd /app
alembic revision --autogenerate -m "Initial schema"
```

### 2. Apply Migration
```bash
alembic upgrade head
```

### 3. Check Status
```bash
alembic current
```
