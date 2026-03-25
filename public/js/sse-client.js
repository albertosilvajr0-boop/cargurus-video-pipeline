/**
 * SSE Client - Real-time job progress via Server-Sent Events.
 *
 * Replaces polling pattern with persistent EventSource connections.
 * Falls back to polling if SSE is not supported or connection fails.
 */

const SSEClient = {
    _sources: {},
    _fallbackIntervals: {},

    /**
     * Connect to the all-jobs SSE stream.
     * @param {Function} onUpdate - Called with jobs object when data changes
     * @param {Function} onError - Called on connection error
     */
    connectAllJobs(onUpdate, onError) {
        if (this._sources.allJobs) {
            this._sources.allJobs.close();
        }

        try {
            const source = new EventSource('/api/events/jobs');
            this._sources.allJobs = source;

            source.addEventListener('jobs', (event) => {
                try {
                    const jobs = JSON.parse(event.data);
                    onUpdate(jobs);
                } catch (e) {
                    console.warn('SSE parse error:', e);
                }
            });

            source.addEventListener('error', (event) => {
                console.warn('SSE connection error, falling back to polling');
                source.close();
                delete this._sources.allJobs;
                this._startPollingFallback('allJobs', '/api/jobs', onUpdate, onError);
            });

        } catch (e) {
            // SSE not supported, fall back to polling
            this._startPollingFallback('allJobs', '/api/jobs', onUpdate, onError);
        }
    },

    /**
     * Connect to a single job's SSE stream.
     * @param {string} jobId - The job ID to track
     * @param {Function} onUpdate - Called with job data on each update
     * @param {Function} onComplete - Called when job reaches terminal state
     */
    connectJob(jobId, onUpdate, onComplete) {
        const key = `job_${jobId}`;
        if (this._sources[key]) {
            this._sources[key].close();
        }

        try {
            const source = new EventSource(`/api/events/job/${jobId}`);
            this._sources[key] = source;

            source.addEventListener('job', (event) => {
                try {
                    const job = JSON.parse(event.data);
                    onUpdate(job);
                } catch (e) {
                    console.warn('SSE parse error:', e);
                }
            });

            source.addEventListener('done', (event) => {
                try {
                    const job = JSON.parse(event.data);
                    if (onComplete) onComplete(job);
                } catch (e) {
                    console.warn('SSE parse error:', e);
                }
                source.close();
                delete this._sources[key];
            });

            source.addEventListener('error', () => {
                source.close();
                delete this._sources[key];
                // Fall back to polling for this job
                this._startJobPollingFallback(jobId, onUpdate, onComplete);
            });

        } catch (e) {
            this._startJobPollingFallback(jobId, onUpdate, onComplete);
        }
    },

    /**
     * Connect to a batch's SSE stream.
     * @param {string} batchId - The batch ID to track
     * @param {Function} onUpdate - Called with batch progress data
     * @param {Function} onComplete - Called when batch is done
     */
    connectBatch(batchId, onUpdate, onComplete) {
        const key = `batch_${batchId}`;
        if (this._sources[key]) {
            this._sources[key].close();
        }

        try {
            const source = new EventSource(`/api/events/batch/${batchId}`);
            this._sources[key] = source;

            source.addEventListener('batch', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    onUpdate(data);
                } catch (e) {
                    console.warn('SSE batch parse error:', e);
                }
            });

            source.addEventListener('done', (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (onComplete) onComplete(data);
                } catch (e) {
                    console.warn('SSE batch parse error:', e);
                }
                source.close();
                delete this._sources[key];
            });

            source.addEventListener('error', () => {
                source.close();
                delete this._sources[key];
            });

        } catch (e) {
            console.warn('SSE not available for batch tracking');
        }
    },

    /**
     * Disconnect all SSE streams.
     */
    disconnectAll() {
        Object.values(this._sources).forEach(source => source.close());
        this._sources = {};
        Object.values(this._fallbackIntervals).forEach(clearInterval);
        this._fallbackIntervals = {};
    },

    /**
     * Disconnect a specific stream.
     */
    disconnect(key) {
        if (this._sources[key]) {
            this._sources[key].close();
            delete this._sources[key];
        }
        if (this._fallbackIntervals[key]) {
            clearInterval(this._fallbackIntervals[key]);
            delete this._fallbackIntervals[key];
        }
    },

    // --- Polling fallbacks ---

    _startPollingFallback(key, url, onUpdate, onError) {
        if (this._fallbackIntervals[key]) return;
        this._fallbackIntervals[key] = setInterval(async () => {
            try {
                const resp = await fetch(url);
                if (resp.ok) {
                    const data = await resp.json();
                    onUpdate(data);
                }
            } catch (e) {
                if (onError) onError(e);
            }
        }, 3000);
    },

    _startJobPollingFallback(jobId, onUpdate, onComplete) {
        const key = `job_poll_${jobId}`;
        if (this._fallbackIntervals[key]) return;
        this._fallbackIntervals[key] = setInterval(async () => {
            try {
                const resp = await fetch(`/api/job/${jobId}`);
                if (resp.ok) {
                    const job = await resp.json();
                    onUpdate(job);
                    if (job.status === 'complete' || job.status === 'error') {
                        clearInterval(this._fallbackIntervals[key]);
                        delete this._fallbackIntervals[key];
                        if (onComplete) onComplete(job);
                    }
                }
            } catch (e) {
                console.warn('Polling error:', e);
            }
        }, 2500);
    },
};

// Export for use in dashboard
window.SSEClient = SSEClient;
