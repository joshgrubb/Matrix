"""
Cost service — the single source of truth for all cost calculations.

No template, route, or other service calculates costs.  Every cost
figure displayed on the dashboard, in reports, or in exports comes
from this module.

Cost calculation rules:
  - **Per-user software:** quantity × cost_per_license × authorized_count.
  - **Tenant software:**   total_cost ÷ covered_headcount × authorized_count.
    Covered headcount is the union of all positions within the software's
    coverage rows to avoid double-counting.
  - **Hardware:**          quantity × estimated_cost × authorized_count.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.models.equipment import Software, SoftwareCoverage
from app.models.organization import Department, Division, Position
from app.models.requirement import PositionHardware, PositionSoftware

logger = logging.getLogger(__name__)

# Constant for zero-cost comparisons and defaults.
ZERO = Decimal("0.00")


# =========================================================================
# Data classes for structured cost results
# =========================================================================

@dataclass
class HardwareCostLine:
    """A single hardware cost line item for a position."""

    hardware_type_id: int
    hardware_type_name: str
    quantity: int
    unit_cost: Decimal
    line_total: Decimal  # quantity × unit_cost (per person)
    position_total: Decimal  # line_total × authorized_count


@dataclass
class SoftwareCostLine:
    """A single software cost line item for a position."""

    software_id: int
    software_name: str
    license_model: str
    quantity: int
    unit_cost: Decimal  # Per-user: cost_per_license.  Tenant: allocated share.
    line_total: Decimal  # Per person.
    position_total: Decimal  # line_total × authorized_count


@dataclass
class PositionCostSummary:
    """Complete cost breakdown for a single position."""

    position_id: int
    position_title: str
    position_code: str
    division_id: int
    division_name: str
    department_id: int
    department_name: str
    authorized_count: int
    hardware_lines: list[HardwareCostLine] = field(default_factory=list)
    software_lines: list[SoftwareCostLine] = field(default_factory=list)
    hardware_total_per_person: Decimal = ZERO
    software_total_per_person: Decimal = ZERO
    total_per_person: Decimal = ZERO
    hardware_total: Decimal = ZERO
    software_total: Decimal = ZERO
    grand_total: Decimal = ZERO


@dataclass
class DivisionCostSummary:
    """Aggregated costs for a division."""

    division_id: int
    division_name: str
    department_id: int
    department_name: str
    position_count: int
    total_authorized: int
    hardware_total: Decimal = ZERO
    software_total: Decimal = ZERO
    grand_total: Decimal = ZERO


@dataclass
class DepartmentCostSummary:
    """Aggregated costs for a department."""

    department_id: int
    department_name: str
    division_count: int
    position_count: int
    total_authorized: int
    hardware_total: Decimal = ZERO
    software_total: Decimal = ZERO
    grand_total: Decimal = ZERO


@dataclass
class OrganizationCostSummary:
    """Top-level org-wide cost totals."""

    department_count: int
    division_count: int
    position_count: int
    total_authorized: int
    hardware_total: Decimal = ZERO
    software_total: Decimal = ZERO
    grand_total: Decimal = ZERO


# =========================================================================
# Position-level cost calculation (core logic)
# =========================================================================

def calculate_position_cost(position_id: int) -> PositionCostSummary:
    """
    Calculate the full cost breakdown for a single position.

    This is the foundational calculation — all higher-level summaries
    aggregate from position-level results.

    Args:
        position_id: The position to calculate costs for.

    Returns:
        A PositionCostSummary with hardware and software line items.

    Raises:
        ValueError: If the position is not found.
    """
    position = db.session.get(Position, position_id)
    if position is None:
        raise ValueError(f"Position ID {position_id} not found.")

    division = position.division
    department = division.department

    summary = PositionCostSummary(
        position_id=position.id,
        position_title=position.position_title,
        position_code=position.position_code,
        division_id=division.id,
        division_name=division.division_name,
        department_id=department.id,
        department_name=department.department_name,
        authorized_count=position.authorized_count,
    )

    # -- Hardware costs ----------------------------------------------------
    hw_reqs = (
        PositionHardware.query
        .filter_by(position_id=position_id)
        .all()
    )
    for req in hw_reqs:
        hw_type = req.hardware_type
        unit_cost = hw_type.estimated_cost or ZERO
        line_total = Decimal(req.quantity) * unit_cost
        position_total = line_total * Decimal(position.authorized_count)

        summary.hardware_lines.append(
            HardwareCostLine(
                hardware_type_id=hw_type.id,
                hardware_type_name=hw_type.type_name,
                quantity=req.quantity,
                unit_cost=unit_cost,
                line_total=line_total,
                position_total=position_total,
            )
        )
        summary.hardware_total_per_person += line_total
        summary.hardware_total += position_total

    # -- Software costs ----------------------------------------------------
    sw_reqs = (
        PositionSoftware.query
        .filter_by(position_id=position_id)
        .all()
    )
    for req in sw_reqs:
        sw = req.software
        if sw.license_model == "per_user":
            unit_cost = sw.cost_per_license or ZERO
            line_total = Decimal(req.quantity) * unit_cost
        else:
            # Tenant: calculate allocated share.
            unit_cost = _calculate_tenant_share_for_position(sw, position)
            line_total = unit_cost  # Already per-person.

        position_total = line_total * Decimal(position.authorized_count)

        summary.software_lines.append(
            SoftwareCostLine(
                software_id=sw.id,
                software_name=sw.name,
                license_model=sw.license_model,
                quantity=req.quantity,
                unit_cost=unit_cost,
                line_total=line_total,
                position_total=position_total,
            )
        )
        summary.software_total_per_person += line_total
        summary.software_total += position_total

    # -- Totals ------------------------------------------------------------
    summary.total_per_person = (
        summary.hardware_total_per_person + summary.software_total_per_person
    )
    summary.grand_total = summary.hardware_total + summary.software_total

    return summary


# =========================================================================
# Aggregated cost calculations
# =========================================================================

def calculate_division_costs(division_id: int) -> DivisionCostSummary:
    """Aggregate costs across all active positions in a division."""
    division = db.session.get(Division, division_id)
    if division is None:
        raise ValueError(f"Division ID {division_id} not found.")

    positions = (
        Position.query
        .filter_by(division_id=division_id, is_active=True)
        .all()
    )

    summary = DivisionCostSummary(
        division_id=division.id,
        division_name=division.division_name,
        department_id=division.department_id,
        department_name=division.department.department_name,
        position_count=len(positions),
        total_authorized=sum(p.authorized_count for p in positions),
    )

    for position in positions:
        pos_cost = calculate_position_cost(position.id)
        summary.hardware_total += pos_cost.hardware_total
        summary.software_total += pos_cost.software_total

    summary.grand_total = summary.hardware_total + summary.software_total
    return summary


def calculate_department_costs(department_id: int) -> DepartmentCostSummary:
    """Aggregate costs across all active divisions in a department."""
    department = db.session.get(Department, department_id)
    if department is None:
        raise ValueError(f"Department ID {department_id} not found.")

    divisions = (
        Division.query
        .filter_by(department_id=department_id, is_active=True)
        .all()
    )

    summary = DepartmentCostSummary(
        department_id=department.id,
        department_name=department.department_name,
        division_count=len(divisions),
        position_count=0,
        total_authorized=0,
    )

    for division in divisions:
        div_cost = calculate_division_costs(division.id)
        summary.position_count += div_cost.position_count
        summary.total_authorized += div_cost.total_authorized
        summary.hardware_total += div_cost.hardware_total
        summary.software_total += div_cost.software_total

    summary.grand_total = summary.hardware_total + summary.software_total
    return summary


def calculate_organization_costs() -> OrganizationCostSummary:
    """Calculate org-wide cost totals across all active departments."""
    departments = Department.query.filter_by(is_active=True).all()

    summary = OrganizationCostSummary(
        department_count=len(departments),
        division_count=0,
        position_count=0,
        total_authorized=0,
    )

    for dept in departments:
        dept_cost = calculate_department_costs(dept.id)
        summary.division_count += dept_cost.division_count
        summary.position_count += dept_cost.position_count
        summary.total_authorized += dept_cost.total_authorized
        summary.hardware_total += dept_cost.hardware_total
        summary.software_total += dept_cost.software_total

    summary.grand_total = summary.hardware_total + summary.software_total
    return summary


def get_department_cost_breakdown(
    user=None,
) -> list[DepartmentCostSummary]:
    """
    Return a list of department-level cost summaries.

    If a user is provided, results are scope-filtered.
    """
    from app.services import organization_service  # pylint: disable=import-outside-toplevel

    if user is not None:
        departments = organization_service.get_departments(user)
    else:
        departments = Department.query.filter_by(is_active=True).all()

    results = []
    for dept in departments:
        results.append(calculate_department_costs(dept.id))
    return results


# =========================================================================
# Tenant software cost distribution (internal helper)
# =========================================================================

def _calculate_tenant_share_for_position(
    software: Software,
    position: Position,
) -> Decimal:
    """
    Calculate the per-person tenant software cost share for a position.

    The denominator is the total authorized headcount across ALL
    positions covered by this software's coverage rows (using set
    union to prevent double-counting).

    Returns:
        Decimal cost allocated per person in this position.
    """
    if not software.total_cost:
        return ZERO

    # Gather all covered position IDs using set union.
    covered_position_ids = _get_covered_position_ids(software.id)

    if not covered_position_ids:
        return ZERO

    # Sum authorized_count across covered positions.
    total_headcount = (
        db.session.query(func.coalesce(func.sum(Position.authorized_count), 0))
        .filter(
            Position.id.in_(covered_position_ids),
            Position.is_active.is_(True),
        )
        .scalar()
    )

    if total_headcount == 0:
        return ZERO

    # Cost per seat = total_cost / total_headcount.
    return software.total_cost / Decimal(total_headcount)


def _get_covered_position_ids(software_id: int) -> set[int]:
    """
    Return the set of position IDs covered by a tenant-licensed software.

    Uses set union across all coverage rows to prevent double-counting
    when a position falls under multiple coverage definitions.
    """
    coverage_rows = (
        SoftwareCoverage.query
        .filter_by(software_id=software_id)
        .all()
    )

    position_ids = set()
    for cov in coverage_rows:
        if cov.scope_type == "organization":
            # All active positions in the org.
            rows = (
                db.session.query(Position.id)
                .filter(Position.is_active.is_(True))
                .all()
            )
            position_ids.update(r[0] for r in rows)

        elif cov.scope_type == "department" and cov.department_id:
            rows = (
                db.session.query(Position.id)
                .join(Division)
                .filter(
                    Division.department_id == cov.department_id,
                    Position.is_active.is_(True),
                )
                .all()
            )
            position_ids.update(r[0] for r in rows)

        elif cov.scope_type == "division" and cov.division_id:
            rows = (
                db.session.query(Position.id)
                .filter(
                    Position.division_id == cov.division_id,
                    Position.is_active.is_(True),
                )
                .all()
            )
            position_ids.update(r[0] for r in rows)

        elif cov.scope_type == "position" and cov.position_id:
            position_ids.add(cov.position_id)

    return position_ids
