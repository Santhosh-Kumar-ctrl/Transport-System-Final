"""Imports every module's models so `Base.metadata` is complete.

Used by Alembic (autogenerate) and tests (create_all). When you add a module with
tables, add its models import here.
"""

from app.core.events import DomainEvent  # noqa: F401
from app.core.models import Base
from app.modules.allocation import models as _allocation  # noqa: F401
from app.modules.auth import models as _auth  # noqa: F401
from app.modules.boarding import models as _boarding  # noqa: F401
from app.modules.delay_monitor import models as _delay  # noqa: F401
from app.modules.master_data import models as _master  # noqa: F401
from app.modules.notifications import models as _notifications  # noqa: F401
from app.modules.reports import models as _reports  # noqa: F401
from app.modules.tracking import models as _tracking  # noqa: F401
from app.modules.trips import models as _trips  # noqa: F401

metadata = Base.metadata
