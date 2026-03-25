/**
 * Batch/Bulk Video Generation UI Module.
 *
 * Handles CSV upload, VIN list submission, and batch progress tracking.
 * Uses SSE for real-time progress updates.
 */

const BatchUI = {
    currentBatchId: null,

    /**
     * Initialize batch UI event listeners.
     */
    init() {
        const csvZone = document.getElementById('csvUploadZone');
        const csvInput = document.getElementById('csvFileInput');
        const vinTextarea = document.getElementById('batchVinList');

        if (csvZone) {
            csvZone.addEventListener('click', () => csvInput && csvInput.click());
            csvZone.addEventListener('dragover', (e) => {
                e.preventDefault();
                csvZone.classList.add('dragover');
            });
            csvZone.addEventListener('dragleave', () => csvZone.classList.remove('dragover'));
            csvZone.addEventListener('drop', (e) => {
                e.preventDefault();
                csvZone.classList.remove('dragover');
                if (e.dataTransfer.files.length) {
                    csvInput.files = e.dataTransfer.files;
                    BatchUI.onCsvSelected(e.dataTransfer.files[0]);
                }
            });
        }

        if (csvInput) {
            csvInput.addEventListener('change', (e) => {
                if (e.target.files.length) {
                    BatchUI.onCsvSelected(e.target.files[0]);
                }
            });
        }

        if (vinTextarea) {
            vinTextarea.addEventListener('input', () => BatchUI.updateBatchSubmitState());
        }
    },

    /**
     * Handle CSV file selection.
     */
    onCsvSelected(file) {
        const preview = document.getElementById('csvPreview');
        if (!preview) return;

        const reader = new FileReader();
        reader.onload = (e) => {
            const lines = e.target.result.split('\n').filter(l => l.trim());
            const count = Math.max(0, lines.length - 1); // subtract header
            preview.innerHTML = `
                <div style="padding:12px;background:var(--surface);border:1px solid var(--border);border-radius:8px;margin-top:12px">
                    <span style="color:var(--green);font-weight:600">${file.name}</span>
                    <span style="color:var(--muted);margin-left:12px">~${count} vehicles detected</span>
                </div>
            `;
            BatchUI.updateBatchSubmitState();
        };
        reader.readAsText(file);
    },

    /**
     * Update submit button state based on input.
     */
    updateBatchSubmitState() {
        const csvInput = document.getElementById('csvFileInput');
        const vinTextarea = document.getElementById('batchVinList');
        const submitBtn = document.getElementById('batchSubmitBtn');

        if (!submitBtn) return;

        const hasCsv = csvInput && csvInput.files && csvInput.files.length > 0;
        const hasVins = vinTextarea && vinTextarea.value.trim().length > 10;

        submitBtn.disabled = !hasCsv && !hasVins;
    },

    /**
     * Submit batch for processing.
     */
    async submit() {
        const csvInput = document.getElementById('csvFileInput');
        const vinTextarea = document.getElementById('batchVinList');
        const submitBtn = document.getElementById('batchSubmitBtn');

        if (submitBtn) submitBtn.disabled = true;

        try {
            let response;

            if (csvInput && csvInput.files && csvInput.files.length > 0) {
                // CSV upload mode
                const formData = new FormData();
                formData.append('csv', csvInput.files[0]);

                // Add optional params
                const templateSelect = document.getElementById('batchTemplateSelect');
                if (templateSelect && templateSelect.value) {
                    formData.append('prompt_template_id', templateSelect.value);
                }
                const phone = document.getElementById('batchPhone');
                if (phone && phone.value) formData.append('dealer_phone', phone.value);
                const address = document.getElementById('batchAddress');
                if (address && address.value) formData.append('dealer_address', address.value);
                const cta = document.getElementById('batchCTA');
                if (cta && cta.value) formData.append('cta_text', cta.value);

                response = await fetch('/api/batch/upload-csv', {
                    method: 'POST',
                    body: formData,
                });
            } else if (vinTextarea && vinTextarea.value.trim()) {
                // VIN list mode
                const vins = vinTextarea.value
                    .split(/[\n,;]+/)
                    .map(v => v.trim())
                    .filter(v => v.length >= 17);

                response = await fetch('/api/batch/vins', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ vins }),
                });
            } else {
                alert('Please upload a CSV or enter VINs');
                return;
            }

            const result = await response.json();

            if (!response.ok) {
                alert(result.error || 'Batch submission failed');
                if (submitBtn) submitBtn.disabled = false;
                return;
            }

            BatchUI.currentBatchId = result.batch_id;
            BatchUI.showBatchProgress(result);

            // Connect SSE for real-time updates
            if (window.SSEClient) {
                SSEClient.connectBatch(
                    result.batch_id,
                    (data) => BatchUI.updateBatchProgress(data),
                    (data) => BatchUI.onBatchComplete(data)
                );
            }

        } catch (e) {
            alert('Error submitting batch: ' + e.message);
            if (submitBtn) submitBtn.disabled = false;
        }
    },

    /**
     * Show batch progress UI.
     */
    showBatchProgress(result) {
        const form = document.getElementById('batchForm');
        const progress = document.getElementById('batchProgress');

        if (form) form.style.display = 'none';
        if (progress) {
            progress.style.display = 'block';
            progress.innerHTML = `
                <h2 style="margin-bottom:16px">Batch Processing</h2>
                <div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                        <span style="font-size:1.1rem;font-weight:600">Processing ${result.total} vehicles</span>
                        <span id="batchPercent" style="color:var(--accent);font-weight:600">0%</span>
                    </div>
                    <div style="height:8px;background:var(--border);border-radius:4px;overflow:hidden;margin-bottom:16px">
                        <div id="batchProgressBar" style="height:100%;background:var(--accent);border-radius:4px;transition:width 0.5s ease;width:0%"></div>
                    </div>
                    <div id="batchCurrentVehicle" style="color:var(--muted);font-size:0.85rem">Starting...</div>
                    <div id="batchStats" style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:16px">
                        <div class="stat-card"><div class="label">Completed</div><div class="value green" id="batchCompleted">0</div></div>
                        <div class="stat-card"><div class="label">Failed</div><div class="value" id="batchFailed" style="color:var(--red)">0</div></div>
                        <div class="stat-card"><div class="label">Remaining</div><div class="value cyan" id="batchRemaining">${result.total}</div></div>
                    </div>
                </div>
            `;
        }
    },

    /**
     * Update batch progress from SSE data.
     */
    updateBatchProgress(data) {
        const bar = document.getElementById('batchProgressBar');
        const pct = document.getElementById('batchPercent');
        const current = document.getElementById('batchCurrentVehicle');
        const completed = document.getElementById('batchCompleted');
        const failed = document.getElementById('batchFailed');
        const remaining = document.getElementById('batchRemaining');

        if (bar) bar.style.width = data.percent + '%';
        if (pct) pct.textContent = data.percent + '%';
        if (current && data.current_job) {
            current.textContent = `Vehicle ${(data.completed || 0) + (data.failed || 0) + 1} of ${data.total}: ${data.current_job.progress || data.current_job.vin || ''}`;
        }
        if (completed) completed.textContent = data.completed || 0;
        if (failed) failed.textContent = data.failed || 0;
        if (remaining) remaining.textContent = Math.max(0, data.total - (data.completed || 0) - (data.failed || 0));
    },

    /**
     * Handle batch completion.
     */
    onBatchComplete(data) {
        const bar = document.getElementById('batchProgressBar');
        const pct = document.getElementById('batchPercent');
        const current = document.getElementById('batchCurrentVehicle');

        if (bar) {
            bar.style.width = '100%';
            bar.style.background = 'var(--green)';
        }
        if (pct) pct.textContent = '100%';
        if (current) {
            current.innerHTML = `
                <span style="color:var(--green);font-weight:600">Batch complete!</span>
                ${data.completed} videos generated, ${data.failed} failed.
                <button class="btn" style="margin-left:12px" onclick="BatchUI.resetBatch()">Start New Batch</button>
            `;
        }
    },

    /**
     * Reset batch UI for a new submission.
     */
    resetBatch() {
        const form = document.getElementById('batchForm');
        const progress = document.getElementById('batchProgress');

        if (form) form.style.display = 'block';
        if (progress) progress.style.display = 'none';

        const csvInput = document.getElementById('csvFileInput');
        if (csvInput) csvInput.value = '';
        const preview = document.getElementById('csvPreview');
        if (preview) preview.innerHTML = '';
        const vinTextarea = document.getElementById('batchVinList');
        if (vinTextarea) vinTextarea.value = '';

        BatchUI.currentBatchId = null;
        BatchUI.updateBatchSubmitState();
    },
};

// Export
window.BatchUI = BatchUI;
