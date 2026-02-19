"""
Model package — imports all models so Alembic and SQLAlchemy can
discover them automatically when ``flask db`` commands are run.

Each model file corresponds to one database schema:
  - organization.py -> org schema
  - equipment.py    -> equip schema
  - requirement.py  -> equip schema (position junction tables)
  - user.py         -> auth schema
  - audit.py        -> audit schema
  - budget.py       -> budget schema
  - asset.py        -> asset schema (Phase 2)
  - itsm.py         -> itsm schema  (Phase 4)
"""

# -- org schema ------------------------------------------------------------
from app.models.organization import (  # noqa: F401
    Department,
    Division,
    Employee,
    Position,
)

# -- equip schema ----------------------------------------------------------
from app.models.equipment import (  # noqa: F401
    HardwareType,
    Software,
    SoftwareCoverage,
    SoftwareFamily,
    SoftwareType,
)
from app.models.requirement import (  # noqa: F401
    PositionHardware,
    PositionSoftware,
)

# -- auth schema -----------------------------------------------------------
from app.models.user import (  # noqa: F401
    Permission,
    Role,
    RolePermission,
    User,
    UserScope,
)

# -- audit schema ----------------------------------------------------------
from app.models.audit import AuditLog, HRSyncLog  # noqa: F401

# -- budget schema ---------------------------------------------------------
from app.models.budget import (  # noqa: F401
    AuthorizedCountHistory,
    CostSnapshot,
    HardwareTypeCostHistory,
    RequirementHistory,
    SoftwareCostHistory,
)

# -- asset schema (Phase 2) -----------------------------------------------
from app.models.asset import (  # noqa: F401
    Asset,
    AssetAssignment,
    Condition,
    Location,
    LocationType,
    Manufacturer,
    OperatingSystem,
)

# -- itsm schema (Phase 4) ------------------------------------------------
from app.models.itsm import (  # noqa: F401
    Category,
    ChangeRequest,
    Impact,
    Incident,
    Priority,
    Severity,
    Status,
    Ticket,
)
