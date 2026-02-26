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
 *
 * Tier 3 UX Changes:
 *   - Unsaved changes warning (#19): beforeunload listener warns
 *     users if they try to navigate away with unsaved changes on
 *     steps 2–3. Suppressed on form submission.
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

    // ── Double-submission prevention ────────────────────────────────
    // Disable all submit buttons after the first click to prevent
    // duplicate form submissions.  The button is re-enabled after
    // 5 seconds as a fallback in case the submission fails and the
    // user needs to retry.
    document.querySelectorAll('form').forEach(function (form) {
        form.addEventListener('submit', function () {
            var buttons = form.querySelectorAll('button[type="submit"], input[type="submit"]');
            buttons.forEach(function (btn) {
                // Skip if the submission was cancelled by a confirm dialog.
                setTimeout(function () {
                    btn.disabled = true;
                    btn.setAttribute('aria-busy', 'true');
                }, 0);

                // Re-enable after 5 seconds as a safety fallback.
                setTimeout(function () {
                    btn.disabled = false;
                    btn.removeAttribute('aria-busy');
                }, 5000);
            });
        });
    });

    // ── Reference to the equipment form (used by multiple features) ─
    var equipmentForm = document.getElementById('equipment-selection-form');


    // ── Empty submission guard (Tier 1, #4) ─────────────────────────
    // On the hardware and software selection forms, if the user tries
    // to submit with zero items checked, show a confirmation dialog
    // warning that this will clear all equipment from the position.
    if (equipmentForm) {
        equipmentForm.addEventListener('submit', function (e) {
            var checkedItems = equipmentForm.querySelectorAll(
                '.item-selector:checked'
            );
            if (checkedItems.length === 0) {
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
        function syncQuantityDisabled(selector) {
            var row = selector.closest('tr');
            if (!row) { return; }
            var qtyInput = row.querySelector('.item-quantity');
            if (!qtyInput) { return; }

            // For single-select (radio) types, quantity is always
            // readonly and kept at 1, so just toggle opacity.
            var isSingleSelect = selector.getAttribute('data-max-selections') === '1';

            if (selector.checked) {
                // Selected: make quantity visible.
                if (isSingleSelect) {
                    qtyInput.value = 1;
                    qtyInput.style.opacity = '';
                } else {
                    qtyInput.disabled = false;
                    qtyInput.style.opacity = '';
                }
            } else {
                // Deselected: dim the quantity.
                if (isSingleSelect) {
                    qtyInput.style.opacity = '0.4';
                } else {
                    qtyInput.disabled = true;
                    qtyInput.style.opacity = '0.4';
                }
            }
        }

        // Set initial state on page load for all selectors
        // (checkboxes and radio buttons).
        equipmentForm.querySelectorAll('.item-selector').forEach(function (el) {
            syncQuantityDisabled(el);
        });

        // Listen for changes to toggle quantity inputs.
        equipmentForm.addEventListener('change', function (e) {
            if (e.target.classList.contains('item-checkbox')) {
                // Checkbox: just sync the one that changed.
                syncQuantityDisabled(e.target);
            } else if (e.target.classList.contains('item-radio')) {
                // Radio: re-sync ALL radios with the same name,
                // because the browser deselected the previous one.
                var groupName = e.target.getAttribute('name');
                equipmentForm.querySelectorAll(
                    'input[name="' + groupName + '"]'
                ).forEach(function (radio) {
                    syncQuantityDisabled(radio);
                });
            }
        });

        // Before form submission, re-enable all quantity inputs so
        // their values are included in the POST data.  Radio-button
        // rows use readonly (not disabled) so they're always submitted.
        equipmentForm.addEventListener('submit', function () {
            equipmentForm.querySelectorAll('.item-quantity').forEach(function (input) {
                input.disabled = false;
            });
        });
    }
    // ── Max-selections enforcement for types with limits > 1 ────
    // Caps the TOTAL quantity across all checked items in a type
    // group.  For example, if monitors have max_selections = 2,
    // the user can pick 1 monitor at qty 2, or 2 monitors at
    // qty 1 each, but not 1 monitor at qty 3.

    /**
     * Enforce total quantity cap on a type group within an
     * accordion panel.
     *
     * Counts total quantity across all checked items in the group,
     * then adjusts the max attribute on each quantity input and
     * disables unchecked checkboxes when the cap is fully consumed.
     *
     * @param {HTMLElement} trigger - The element that changed
     *     (checkbox or quantity input).
     */
    function enforceMaxSelections(trigger) {
        // Walk up to the accordion-body to scope by type group.
        var group = trigger.closest('.accordion-body');
        if (!group) { return; }

        // Read max_selections from any checkbox in the group.
        var anyCheckbox = group.querySelector(
            '.item-checkbox[data-max-selections]'
        );
        if (!anyCheckbox) { return; }

        var maxSel = parseInt(
            anyCheckbox.getAttribute('data-max-selections'), 10
        );
        if (!maxSel || maxSel <= 1) { return; }

        // Sum total quantity across all checked items in this group.
        var totalQty = 0;
        var checkedRows = [];
        var uncheckedBoxes = [];

        group.querySelectorAll('.item-checkbox').forEach(function (cb) {
            var row = cb.closest('tr');
            if (!row) { return; }
            var qtyInput = row.querySelector('.item-quantity');

            if (cb.checked && qtyInput) {
                var qty = parseInt(qtyInput.value, 10) || 1;
                totalQty += qty;
                checkedRows.push({ checkbox: cb, qtyInput: qtyInput, qty: qty });
            } else {
                uncheckedBoxes.push(cb);
            }
        });

        // Remaining capacity available for new selections.
        var remaining = maxSel - totalQty;

        // Disable unchecked checkboxes if no capacity remains.
        uncheckedBoxes.forEach(function (cb) {
            cb.disabled = (remaining < 1);
        });

        // Cap each checked item's quantity input max so the user
        // can't exceed the group total.  Each item's max = its
        // current qty + remaining capacity.
        checkedRows.forEach(function (entry) {
            var itemMax = entry.qty + remaining;
            // Never allow less than 1 or more than the group cap.
            itemMax = Math.max(1, Math.min(itemMax, maxSel));
            entry.qtyInput.setAttribute('max', itemMax);

            // If the current value somehow exceeds the new max,
            // clamp it down.
            if (entry.qty > itemMax) {
                entry.qtyInput.value = itemMax;
            }
        });
    }

    // Run on page load for pre-populated selections.
    equipmentForm.querySelectorAll(
        '.item-checkbox[data-max-selections]'
    ).forEach(function (cb) {
        enforceMaxSelections(cb);
    });

    // Run on checkbox change.
    equipmentForm.addEventListener('change', function (e) {
        if (e.target.classList.contains('item-checkbox') &&
            e.target.hasAttribute('data-max-selections')) {
            enforceMaxSelections(e.target);
        }
    });

    // Run on quantity input change — this is the key addition
    // so adjusting qty on one item recalculates the cap for the
    // whole group.
    equipmentForm.addEventListener('input', function (e) {
        if (e.target.classList.contains('item-quantity')) {
            enforceMaxSelections(e.target);
        }
    });

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

            // Find all checked equipment selectors (checkboxes + radios).
            var selectors = equipmentForm
                ? equipmentForm.querySelectorAll('.item-selector')
                : [];

            selectors.forEach(function (cb) {
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
                if (e.target.classList.contains('item-selector') ||
                    e.target.classList.contains('item-checkbox') ||
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


    // ── Tier 3 (#19): Unsaved changes warning ──────────────────────
    // On steps 2 and 3 (hardware and software selection), capture the
    // initial state of all checkboxes and quantities.  If the user
    // navigates away (via back button, closing the tab, or clicking
    // a link) without submitting, show the browser's native "unsaved
    // changes" dialog.
    //
    // The guard is suppressed when the form is submitted (the user
    // clicked "Save & Next" or "Save & View Summary"), so it only
    // fires for accidental navigation.
    if (equipmentForm) {
        /**
         * Build a snapshot string of the current form state.
         * Format: "hwId:checked:qty,hwId:checked:qty,..."
         * This is compared against the initial snapshot to detect changes.
         *
         * @returns {string} Serialized form state for comparison.
         */
        function captureFormState() {
            var parts = [];
            equipmentForm.querySelectorAll('.item-checkbox').forEach(function (cb) {
                var row = cb.closest('tr');
                var qtyInput = row ? row.querySelector('.item-quantity') : null;
                var qty = qtyInput ? qtyInput.value : '1';
                parts.push(cb.name + ':' + cb.checked + ':' + qty);
            });
            return parts.join(',');
        }

        // Snapshot the state at page load.
        var initialFormState = captureFormState();

        // Track whether the form was submitted (to suppress the warning).
        var formIsSubmitting = false;

        equipmentForm.addEventListener('submit', function () {
            formIsSubmitting = true;
        });

        // Warn on navigation away if state has changed.
        window.addEventListener('beforeunload', function (e) {
            if (formIsSubmitting) {
                return;  // Form submission — do not warn.
            }
            var currentState = captureFormState();
            if (currentState !== initialFormState) {
                // Standard browser behavior: set returnValue to trigger
                // the native "Leave site?" dialog.  The actual message
                // text is ignored by modern browsers.
                e.preventDefault();
                e.returnValue = '';
            }
        });
    }

});