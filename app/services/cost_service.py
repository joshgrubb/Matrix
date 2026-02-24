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
    Cost is sourced from ``equip.hardware.estimated_cost`` (specific item).
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

    hardware_id: int
    hardware_name: str
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
    # Now references Hardware (specific item), not HardwareType.
    hw_reqs = PositionHardware.query.filter_by(position_id=position_id).all()
    for req in hw_reqs:
        hw = req.hardware  # The specific Hardware item.
        hw_type = hw.hardware_type  # The parent category.
        unit_cost = hw.estimated_cost or ZERO
        line_total = Decimal(req.quantity) * unit_cost
        position_total = line_total * Decimal(position.authorized_count)

        summary.hardware_lines.append(
            HardwareCostLine(
                hardware_id=hw.id,
                hardware_name=hw.name,
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
    sw_reqs = PositionSoftware.query.filter_by(position_id=position_id).all()
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

    # -- Per-person and grand totals ---------------------------------------
    summary.total_per_person = (
        summary.hardware_total_per_person + summary.software_total_per_person
    )
    summary.grand_total = summary.hardware_total + summary.software_total

    return summary


# =========================================================================
# Division-level aggregation
# =========================================================================


def get_division_cost_breakdown(division_id: int) -> DivisionCostSummary:
    """
    Aggregate costs for all positions within a division.

    Args:
        division_id: The division to aggregate.

    Returns:
        A DivisionCostSummary with hardware and software totals.
    """
    division = db.session.get(Division, division_id)
    if division is None:
        raise ValueError(f"Division ID {division_id} not found.")

    positions = Position.query.filter_by(division_id=division_id, is_active=True).all()

    summary = DivisionCostSummary(
        division_id=division.id,
        division_name=division.division_name,
        department_id=division.department_id,
        department_name=division.department.department_name,
        position_count=len(positions),
        total_authorized=sum(p.authorized_count for p in positions),
    )

    for pos in positions:
        pos_cost = calculate_position_cost(pos.id)
        summary.hardware_total += pos_cost.hardware_total
        summary.software_total += pos_cost.software_total

    summary.grand_total = summary.hardware_total + summary.software_total
    return summary


# =========================================================================
# Department-level aggregation
# =========================================================================


def get_department_cost_breakdown(user=None) -> list[DepartmentCostSummary]:
    """
    Build cost summaries for each department the user can access.

    If ``user`` is None, all departments are returned (for CLI / exports).
    Otherwise, departments are filtered by the user's scope.

    Returns:
        List of DepartmentCostSummary ordered by department name.
    """
    # Import here to avoid circular dependency.
    from app.services import (
        organization_service,
    )  # pylint: disable=import-outside-toplevel

    departments = organization_service.get_departments(user)
    summaries = []

    for dept in departments:
        divisions = Division.query.filter_by(
            department_id=dept.id, is_active=True
        ).all()

        dept_summary = DepartmentCostSummary(
            department_id=dept.id,
            department_name=dept.department_name,
            division_count=len(divisions),
            position_count=0,
            total_authorized=0,
        )

        for div in divisions:
            div_cost = get_division_cost_breakdown(div.id)
            dept_summary.position_count += div_cost.position_count
            dept_summary.total_authorized += div_cost.total_authorized
            dept_summary.hardware_total += div_cost.hardware_total
            dept_summary.software_total += div_cost.software_total

        dept_summary.grand_total = (
            dept_summary.hardware_total + dept_summary.software_total
        )
        summaries.append(dept_summary)

    return summaries


# =========================================================================
# Tenant software cost helpers
# =========================================================================


def _calculate_tenant_share_for_position(
    software: Software,
    position: Position,
) -> Decimal:
    """
    Calculate the per-person allocated cost for tenant-licensed software.

    Formula: total_cost / covered_headcount
    where covered_headcount is the unique headcount covered by all
    SoftwareCoverage rows for this software.
    """
    total_cost = software.total_cost or ZERO
    if total_cost == ZERO:
        return ZERO

    covered_headcount = _get_covered_headcount(software)
    if covered_headcount == 0:
        return ZERO

    # Per-person share of the tenant cost.
    return total_cost / Decimal(covered_headcount)


def _get_covered_headcount(software: Software) -> int:
    """
    Calculate total unique headcount covered by a tenant software's
    coverage definitions.

    Sums ``authorized_count`` across all positions that fall within
    any coverage row's scope, deduplicating positions that appear in
    multiple coverage scopes.

    Supported scope_types:
        - organization: All active positions in the org.
        - department:   All active positions under divisions in that department.
        - division:     All active positions in that division.
        - position:     A single specific position.
    """
    coverage_rows = SoftwareCoverage.query.filter_by(software_id=software.id).all()

    if not coverage_rows:
        return 0

    covered_position_ids: set[int] = set()

    for cov in coverage_rows:
        if cov.scope_type == "organization":
            # All active positions in the org.
            all_positions = Position.query.filter_by(is_active=True).all()
            covered_position_ids.update(p.id for p in all_positions)

        elif cov.scope_type == "department" and cov.department_id:
            # All positions in divisions under this department.
            divisions = Division.query.filter_by(
                department_id=cov.department_id, is_active=True
            ).all()
            for div in divisions:
                positions = Position.query.filter_by(
                    division_id=div.id, is_active=True
                ).all()
                covered_position_ids.update(p.id for p in positions)

        elif cov.scope_type == "division" and cov.division_id:
            # All positions in this division.
            positions = Position.query.filter_by(
                division_id=cov.division_id, is_active=True
            ).all()
            covered_position_ids.update(p.id for p in positions)

        elif cov.scope_type == "position" and cov.position_id:
            # A single specific position.
            position = Position.query.filter_by(
                id=cov.position_id, is_active=True
            ).first()
            if position:
                covered_position_ids.add(position.id)

    # Sum authorized_count for all covered positions.
    if not covered_position_ids:
        return 0

    total = (
        db.session.query(func.sum(Position.authorized_count))
        .filter(Position.id.in_(covered_position_ids))
        .scalar()
    )
    return total or 0
