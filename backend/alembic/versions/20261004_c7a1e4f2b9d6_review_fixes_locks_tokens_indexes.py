"""review fixes: one running trip per driver/bus, token versions, payload indexes

* trips: partial unique indexes so a driver, and a bus, has at most one trip in progress (two
  simultaneous "start" requests can no longer both win). Before creating them, any extra running
  trips (only possible through that race) are completed, keeping the newest one per driver/bus.
* users.token_version: bumped on password change or deactivation to end every session.
* domain_events: expression indexes on payload->>'trip_id' and payload->>'route_id' for trip
  timelines and route history.

Revision ID: c7a1e4f2b9d6
Revises: bbb63096dc6b
Create Date: 2026-10-04 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c7a1e4f2b9d6'
down_revision: Union[str, None] = 'bbb63096dc6b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KEEP_NEWEST_RUNNING = """
UPDATE trips SET status = 'completed', ended_at = COALESCE(ended_at, now())
WHERE status = 'in_progress' AND id NOT IN (
    SELECT DISTINCT ON ({col}) id FROM trips WHERE status = 'in_progress'
    ORDER BY {col}, started_at DESC NULLS LAST, id DESC
)
"""


def upgrade() -> None:
    op.execute(_KEEP_NEWEST_RUNNING.format(col="driver_id"))
    op.execute(_KEEP_NEWEST_RUNNING.format(col="bus_id"))
    op.create_index('uq_trips_one_running_per_driver', 'trips', ['driver_id'], unique=True,
                    postgresql_where=sa.text("status = 'in_progress'"))
    op.create_index('uq_trips_one_running_per_bus', 'trips', ['bus_id'], unique=True,
                    postgresql_where=sa.text("status = 'in_progress'"))

    op.add_column('users', sa.Column('token_version', sa.Integer(), server_default='0', nullable=False))

    op.create_index('ix_domain_events_payload_trip_id', 'domain_events', [sa.text("(payload ->> 'trip_id')")])
    op.create_index('ix_domain_events_payload_route_id', 'domain_events', [sa.text("(payload ->> 'route_id')")])


def downgrade() -> None:
    op.drop_index('ix_domain_events_payload_route_id', table_name='domain_events')
    op.drop_index('ix_domain_events_payload_trip_id', table_name='domain_events')
    op.drop_column('users', 'token_version')
    op.drop_index('uq_trips_one_running_per_bus', table_name='trips')
    op.drop_index('uq_trips_one_running_per_driver', table_name='trips')
