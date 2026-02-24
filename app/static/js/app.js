/**
 * PositionMatrix — Custom JavaScript
 *
 * Handles sidebar toggle for mobile, flash message auto-dismiss,
 * confirm dialogs for destructive actions, the client-side running
 * cost panel on equipment selection pages, the empty submission
 * guard for the hardware/software forms, client-side search/filter
 * for accordion item lists, and quantity field enable/disable sync.
 *
 * HTMX is loaded via CDN in base.html and handles most dynamic
 * server-rendered interactions. This file supplements HTMX with
 * functionality it cannot handle natively.
 *
 * Tier 1 UX Changes:
 *   - Flash message timeout: 10 s for success/info; warnings and
 *     errors persist until manually dismissed.
 *   - Empty submission guard: confirms with the user before saving
 *     an empty selection (which would clear all equipment).
 *   - Running cost panel: client-side arithmetic reads data-cost
 *     attributes from table rows and updates a sticky summary.
 *
 * Tier 2 UX Changes:
 *   - Search/filter (#10): Text input filters accordion items by
 *     name and auto-expands matching groups.
 *   - Disable quantity for unchecked items (#13): Quantity inputs
 *     are disabled (grayed out) when the corresponding checkbox is
 *     unchecked, eliminating visual noise.
 */

document.addEventListener('DOMContentLoaded', function () {

    // ── Sidebar mobile toggle ───────────────────────────────────────
    var sidebar = document.getElementById('sidebarNav');
    var toggle = document.getElementById('sidebarToggle');
    var backdrop = document.getElementById('sidebarBackdrop');

    /**
     * Open the mobile sidebar and show the backdrop overlay.
     */
    function openSidebar() {
        if (sidebar) { sidebar.classList.add('show'); }
        if (backdrop) { backdrop.classList.add('show'); }
        document.body.style.overflow = 'hidden';
    }

    /**
     * Close the mobile sidebar and hide the backdrop overlay.
     */
    function closeSidebar() {
        if (sidebar) { sidebar.classList.remove('show'); }
        if (backdrop) { backdrop.classList.remove('show'); }
        document.body.style.overflow = '';
    }

    if (toggle) {
        toggle.addEventListener('click', function () {
            if (sidebar && sidebar.classList.contains('show')) {
                closeSidebar();
            } else {
                openSidebar();
            }
        });
    }

    if (backdrop) {
        backdrop.addEventListener('click', closeSidebar);
    }

    if (sidebar) {
        sidebar.querySelectorAll('.pm-sidebar-link').forEach(function (link) {
            link.addEventListener('click', function () {
                if (window.innerWidth < 992) {
                    closeSidebar();
                }
            });
        });
    }


    // ── Auto-dismiss flash messages ─────────────────────────────────
    // Tier 1 Change: Success and info messages auto-dismiss after 10
    // seconds.  Warnings and errors persist until manually closed.
    var flashAlerts = document.querySelectorAll('#flash-messages .alert');
    flashAlerts.forEach(function (alert) {
        // Only auto-dismiss success and info messages.
        var isTransient = alert.classList.contains('alert-success')
            || alert.classList.contains('alert-info');
        if (isTransient) {
            setTimeout(function () {
                var bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
                if (bsAlert) {
                    bsAlert.close();
                }
            }, 10000);  // 10 seconds (was 6 seconds).
        }
        // Warnings (alert-warning) and errors (alert-danger) stay
        // visible until the user clicks the close button.
    });


    // ── Confirm dialogs for destructive actions ─────────────────────
    // Any element with data-confirm="message" will show a confirm
    // dialog before proceeding. Works on buttons, links, and forms.
    document.addEventListener('click', function (e) {
        var target = e.target.closest('[data-confirm]');
        if (target) {
            var message = target.getAttribute('data-confirm');
            if (!confirm(message)) {
                e.preventDefault();
                e.stopPropagation();
            }
        }
    });


    // ── Reference to the equipment form (used by multiple features) ─
    var equipmentForm = document.getElementById('equipment-selection-form');


    // ── Empty submission guard (Tier 1, #4) ─────────────────────────
    // On the hardware and software selection forms, if the user tries
    // to submit with zero items checked, show a confirmation dialog
    // warning that this will clear all equipment from the position.
    if (equipmentForm) {
        equipmentForm.addEventListener('submit', function (e) {
            var checkedBoxes = equipmentForm.querySelectorAll(
                'input[type="checkbox"]:checked'
            );
            if (checkedBoxes.length === 0) {
                // Determine which step we are on from a data attribute.
                var step = equipmentForm.getAttribute('data-step') || 'equipment';
                var confirmed = confirm(
                    'No items are selected. This will remove all ' +
                    step + ' from this position.\n\nAre you sure?'
                );
                if (!confirmed) {
                    e.preventDefault();
                }
            }
        });
    }


    // ── Tier 2 (#13): Disable quantity for unchecked items ──────────
    // When a checkbox is unchecked, its row's quantity input is
    // disabled and visually grayed out.  When checked, the input
    // is re-enabled.  Disabled inputs are NOT submitted with the
    // form, but since we only parse *_selected keys in the route,
    // this is safe — unchecked items are ignored regardless.
    //
    // NOTE: We must temporarily re-enable all quantity inputs on
    // form submit so that checked items with quantities > 1 are
    // actually included in the POST data.
    if (equipmentForm) {
        /**
         * Sync a single quantity input's disabled state with its
         * row's checkbox.
         *
         * @param {HTMLInputElement} checkbox - The item checkbox.
         */
        function syncQuantityDisabled(checkbox) {
            var row = checkbox.closest('tr');
            if (!row) { return; }
            var qtyInput = row.querySelector('.item-quantity');
            if (!qtyInput) { return; }

            if (checkbox.checked) {
                // Enable the quantity input and remove dim styling.
                qtyInput.disabled = false;
                qtyInput.style.opacity = '';
            } else {
                // Disable the quantity input and dim it.
                qtyInput.disabled = true;
                qtyInput.style.opacity = '0.4';
            }
        }

        // Set initial state on page load for all checkboxes.
        equipmentForm.querySelectorAll('.item-checkbox').forEach(function (cb) {
            syncQuantityDisabled(cb);
        });

        // Listen for checkbox changes to toggle quantity inputs.
        equipmentForm.addEventListener('change', function (e) {
            if (e.target.classList.contains('item-checkbox')) {
                syncQuantityDisabled(e.target);
            }
        });

        // Before form submission, re-enable all quantity inputs so
        // their values are included in the POST data.
        equipmentForm.addEventListener('submit', function () {
            equipmentForm.querySelectorAll('.item-quantity').forEach(function (input) {
                input.disabled = false;
            });
        });
    }


    // ── Running cost panel (Tier 1, #3 — MVP) ──────────────────────
    // Client-side only. Reads data-unit-cost from each table row and
    // multiplies by quantity when checked. Updates a sticky panel.
    // Does NOT alter cost_service or introduce auto-save.
    var costPanel = document.getElementById('running-cost-panel');
    if (costPanel) {
        var authorizedCount = parseInt(
            costPanel.getAttribute('data-authorized-count') || '1', 10
        );
        var totalDisplay = document.getElementById('cost-panel-total');
        var perPersonDisplay = document.getElementById('cost-panel-per-person');
        var itemCountDisplay = document.getElementById('cost-panel-item-count');

        /**
         * Recalculate the running cost total from all checked items.
         *
         * Reads data-unit-cost from each row, multiplies by the
         * quantity input, and sums across all checked items.
         */
        function recalculateCost() {
            var total = 0;
            var itemCount = 0;

            // Find all checked equipment checkboxes in the form.
            var checkboxes = equipmentForm
                ? equipmentForm.querySelectorAll('.item-checkbox')
                : [];

            checkboxes.forEach(function (cb) {
                if (!cb.checked) { return; }

                // Walk up to the table row to find cost data.
                var row = cb.closest('tr');
                if (!row) { return; }

                var unitCost = parseFloat(
                    row.getAttribute('data-unit-cost') || '0'
                );
                // Find the quantity input in the same row.
                var qtyInput = row.querySelector('.item-quantity');
                var qty = qtyInput ? parseInt(qtyInput.value, 10) || 1 : 1;

                total += unitCost * qty;
                itemCount++;
            });

            // Update the panel displays.
            if (perPersonDisplay) {
                perPersonDisplay.textContent = '$' + total.toFixed(2);
            }
            if (totalDisplay) {
                var positionTotal = total * authorizedCount;
                totalDisplay.textContent = '$' + positionTotal.toFixed(2);
            }
            if (itemCountDisplay) {
                itemCountDisplay.textContent = itemCount +
                    ' item' + (itemCount !== 1 ? 's' : '') + ' selected';
            }
        }

        // Attach listeners to all checkboxes and quantity inputs.
        if (equipmentForm) {
            equipmentForm.addEventListener('change', function (e) {
                if (e.target.classList.contains('item-checkbox') ||
                    e.target.classList.contains('item-quantity')) {
                    recalculateCost();
                }
            });
            equipmentForm.addEventListener('input', function (e) {
                if (e.target.classList.contains('item-quantity')) {
                    recalculateCost();
                }
            });
        }

        // Initial calculation on page load.
        recalculateCost();
    }


    // ── Tier 2 (#10): Search / filter for accordion items ───────────
    // A text input at the top of the equipment and software pages
    // filters table rows by item name.  Groups with zero visible
    // rows are hidden entirely.  Groups with matches are auto-
    // expanded so the user sees results immediately.
    var filterInput = document.getElementById('item-filter');
    if (filterInput) {
        filterInput.addEventListener('input', function () {
            var query = this.value.toLowerCase().trim();

            // Iterate over each accordion group.
            document.querySelectorAll('.accordion-item').forEach(function (group) {
                var rows = group.querySelectorAll('tbody tr');
                var visibleCount = 0;

                // Show or hide each row based on whether the item
                // name matches the search query.
                rows.forEach(function (row) {
                    var label = row.querySelector('label');
                    if (!label) { return; }
                    var name = label.textContent.toLowerCase();
                    var match = !query || name.includes(query);
                    row.style.display = match ? '' : 'none';
                    if (match) { visibleCount++; }
                });

                // Hide the entire group if it has no matching rows.
                group.style.display = (visibleCount > 0 || !query) ? '' : 'none';

                // Auto-expand groups that have matches when filtering.
                if (query && visibleCount > 0) {
                    var collapse = group.querySelector('.accordion-collapse');
                    if (collapse && !collapse.classList.contains('show')) {
                        new bootstrap.Collapse(collapse, { toggle: true });
                    }
                }

                // When the filter is cleared, collapse groups back to
                // their default state (only groups with selections open).
                if (!query) {
                    var collapse = group.querySelector('.accordion-collapse');
                    if (collapse) {
                        var hasChecked = group.querySelectorAll(
                            '.item-checkbox:checked'
                        ).length > 0;
                        if (!hasChecked && collapse.classList.contains('show')) {
                            new bootstrap.Collapse(collapse, { toggle: true });
                        }
                    }
                }
            });
        });
    }


    // ── Initialize Bootstrap tooltips ───────────────────────────────
    // Used for inline help on license types, column headers, and
    // other labels (Tier 1 + Tier 2 #14 enhancements).
    var tooltipTriggers = document.querySelectorAll('[data-bs-toggle="tooltip"]');
    tooltipTriggers.forEach(function (el) {
        new bootstrap.Tooltip(el);
    });

});