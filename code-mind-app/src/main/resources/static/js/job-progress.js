(function () {
  const jobId = document.body.dataset.jobId;
  const STEP_ORDER = ['PENDING', 'SCANNING', 'FILTERING', 'PROCESSING', 'COMPLETED'];
  const stepper = document.getElementById('stepper');
  const failureBanner = document.getElementById('failure-banner');
  const filesFeed = document.getElementById('files-feed');
  const failedPanel = document.getElementById('failed-panel');
  const failedFeed = document.getElementById('failed-feed');
  const viewerOverlay = document.getElementById('viewer-overlay');
  const viewerPathLabel = document.getElementById('viewer-path');
  const viewerBody = document.getElementById('viewer-body');
  const btnCancel = document.getElementById('btn-cancel');
  const btnExport = document.getElementById('btn-export');
  let polling = true;
  let failedLoaded = false;

  // ── Stepper ────────────────────────────────────────────────────────────────

  function applyPhase(phase) {
    const isTerminalError = phase === 'FAILED' || phase === 'CANCELLED';
    stepper.classList.toggle('failed', isTerminalError);
    const currentIndex = STEP_ORDER.indexOf(phase);
    stepper.querySelectorAll('.step').forEach((el) => {
      el.classList.remove('done', 'active');
      if (isTerminalError) return;
      const idx = STEP_ORDER.indexOf(el.dataset.phase);
      if (idx < currentIndex) el.classList.add('done');
      else if (idx === currentIndex) el.classList.add('active');
    });
  }

  function setStat(id, value) {
    document.getElementById(id).textContent = value;
  }

  function renderJob(job) {
    applyPhase(job.phase);
    setStat('stat-total', job.totalFiles);
    setStat('stat-processed', job.processedFiles);
    setStat('stat-succeeded', job.succeededFiles);
    setStat('stat-failed', job.failedFiles);
    setStat('stat-skipped', job.skippedFiles);

    if (job.phase === 'FAILED') {
      failureBanner.style.display = 'block';
      failureBanner.textContent = 'Job failed: ' + (job.failureReason || 'unknown error');
    } else if (job.phase === 'CANCELLED') {
      failureBanner.style.display = 'block';
      failureBanner.className = 'failure-banner cancelled-banner';
      failureBanner.textContent = 'Job was cancelled.';
    } else {
      failureBanner.style.display = 'none';
      failureBanner.className = 'failure-banner';
    }

    updateActions(job);

    if (job.phase === 'COMPLETED' || job.phase === 'FAILED' || job.phase === 'CANCELLED') {
      polling = false;
      if (job.failedFiles > 0 && !failedLoaded) {
        failedLoaded = true;
        loadFailedFiles();
      }
    }
  }

  function updateActions(job) {
    const active = job.phase === 'SCANNING' || job.phase === 'FILTERING' || job.phase === 'PROCESSING';
    btnCancel.style.display = active ? 'inline-block' : 'none';
    if (job.phase === 'COMPLETED') {
      btnExport.style.display = 'inline-block';
      btnExport.href = apiUrl('/api/v1/extraction-jobs/' + jobId + '/export');
    } else {
      btnExport.style.display = 'none';
    }
  }

  async function cancelJob() {
    btnCancel.disabled = true;
    btnCancel.textContent = 'Stopping…';
    try {
      await fetch(apiUrl('/api/v1/extraction-jobs/' + jobId + '/cancel'), { method: 'POST' });
    } catch (e) {
      console.error('Cancel request failed', e);
    } finally {
      btnCancel.disabled = false;
      btnCancel.textContent = 'Stop Job';
    }
  }

  btnCancel.addEventListener('click', cancelJob);

  // ── Utilities ──────────────────────────────────────────────────────────────

  function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = String(value || '');
    return div.innerHTML;
  }

  function formatBytes(bytes) {
    return bytes < 1024 ? bytes + ' B' : (bytes / 1024).toFixed(1) + ' KB';
  }

  function stripJsonExt(path) {
    return path.endsWith('.json') ? path.slice(0, -5) : path;
  }

  // ── Recently extracted files feed ──────────────────────────────────────────

  function renderFiles(files) {
    if (!files.length) {
      filesFeed.innerHTML = '<p class="empty-state">Waiting for output files&hellip;</p>';
      return;
    }
    const rows = files.map((f) =>
      '<tr class="file-clickable" data-output-path="' + escapeHtml(f.relativePath) + '">'
      + '<td class="mono">' + escapeHtml(stripJsonExt(f.relativePath)) + '</td>'
      + '<td>' + formatBytes(f.sizeBytes) + '</td>'
      + '<td class="mono">' + new Date(f.modifiedAt).toLocaleTimeString() + '</td>'
      + '</tr>'
    ).join('');
    filesFeed.innerHTML =
      '<table><thead><tr><th>File</th><th>Size</th><th>Updated</th></tr></thead>'
      + '<tbody>' + rows + '</tbody></table>';
  }

  filesFeed.addEventListener('click', (e) => {
    const row = e.target.closest('tr[data-output-path]');
    if (row) openViewer(row.dataset.outputPath);
  });

  // ── Failed files panel ─────────────────────────────────────────────────────

  async function loadFailedFiles() {
    try {
      const res = await fetch(apiUrl('/api/v1/extraction-jobs/' + jobId + '/failed-files'));
      if (!res.ok) return;
      const failed = await res.json();
      if (!failed.length) return;
      renderFailedFiles(failed);
      failedPanel.style.display = 'block';
    } catch (e) {
      console.error('Failed to load failed files', e);
    }
  }

  function renderFailedFiles(files) {
    const rows = files.map((f) => {
      const errShort = f.errorMessage.length > 140
        ? f.errorMessage.slice(0, 140) + '…'
        : f.errorMessage;
      return '<tr class="file-clickable" data-output-path="' + escapeHtml(f.relativePath + '.json') + '">'
        + '<td class="mono">' + escapeHtml(f.relativePath) + '</td>'
        + '<td class="failed-error-cell">' + escapeHtml(errShort) + '</td>'
        + '<td class="mono">' + (f.durationMillis / 1000).toFixed(1) + 's</td>'
        + '</tr>';
    }).join('');
    failedFeed.innerHTML =
      '<table><thead><tr><th>File</th><th>Error</th><th>Time</th></tr></thead>'
      + '<tbody>' + rows + '</tbody></table>';
  }

  failedFeed.addEventListener('click', (e) => {
    const row = e.target.closest('tr[data-output-path]');
    if (row) openViewer(row.dataset.outputPath);
  });

  // ── Viewer modal ───────────────────────────────────────────────────────────

  async function openViewer(outputRelPath) {
    viewerPathLabel.textContent = stripJsonExt(outputRelPath);
    viewerBody.innerHTML = '<p class="viewer-loading">Loading&hellip;</p>';
    viewerOverlay.style.display = 'flex';
    document.body.style.overflow = 'hidden';

    try {
      const res = await fetch(
        apiUrl('/api/v1/extraction-jobs/' + jobId + '/output-file?relativePath=' + encodeURIComponent(outputRelPath))
      );
      if (!res.ok) {
        viewerBody.innerHTML = '<p class="viewer-error-msg">File not found.</p>';
        return;
      }
      const result = await res.json();
      renderViewer(result);
    } catch (e) {
      viewerBody.innerHTML = '<p class="viewer-error-msg">Failed to load: ' + escapeHtml(e.message) + '</p>';
    }
  }

  function closeViewer(e) {
    if (e && e.currentTarget === viewerOverlay && e.target !== viewerOverlay) return;
    viewerOverlay.style.display = 'none';
    document.body.style.overflow = '';
  }

  viewerOverlay.addEventListener('click', closeViewer);
  document.getElementById('viewer-close').addEventListener('click', closeViewer);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && viewerOverlay.style.display !== 'none') closeViewer();
  });

  function renderViewer(result) {
    if (!result.success && !result.skipped) {
      viewerBody.innerHTML =
        '<div class="viewer-error-box">'
        + '<strong>Extraction failed</strong>'
        + '<pre class="viewer-error-pre">' + escapeHtml(result.errorMessage || 'Unknown error') + '</pre>'
        + buildViewerMeta(result)
        + '</div>';
      return;
    }

    if (result.skipped) {
      viewerBody.innerHTML =
        '<div class="viewer-skipped-box">Skipped: ' + escapeHtml(result.errorMessage || '') + '</div>';
      return;
    }

    let extracted;
    try {
      let raw = result.content || '{}';
      // Strip accidental markdown fences the model may have added
      raw = raw.replace(/^```[^\n]*\n?/, '').replace(/\n?```$/, '').trim();
      extracted = JSON.parse(raw);
    } catch {
      viewerBody.innerHTML = '<pre class="viewer-raw">' + escapeHtml(result.content || '') + '</pre>';
      return;
    }

    let html = '';

    if (extracted.summary) {
      html += '<div class="viewer-section">'
        + '<h3 class="viewer-section-title">Summary</h3>'
        + '<p class="viewer-summary">' + escapeHtml(extracted.summary) + '</p>'
        + '</div>';
    }

    if (extracted.rules && extracted.rules.length) {
      html += '<div class="viewer-section">'
        + '<h3 class="viewer-section-title">Business Rules <span class="viewer-count">' + extracted.rules.length + '</span></h3>';
      extracted.rules.forEach((rule) => {
        html += '<div class="rule-card">';
        html += '<div class="rule-name">' + escapeHtml(rule.name || '') + '</div>';
        if (rule.description) {
          html += '<p class="rule-desc">' + escapeHtml(rule.description) + '</p>';
        }
        if (rule.conditions && rule.conditions.length) {
          html += '<div class="rule-group"><span class="rule-label">Conditions</span><ul class="rule-list">';
          rule.conditions.forEach((c) => { html += '<li>' + escapeHtml(c) + '</li>'; });
          html += '</ul></div>';
        }
        if (rule.actions && rule.actions.length) {
          html += '<div class="rule-group"><span class="rule-label">Actions</span><ul class="rule-list">';
          rule.actions.forEach((a) => { html += '<li>' + escapeHtml(a) + '</li>'; });
          html += '</ul></div>';
        }
        html += '</div>';
      });
      html += '</div>';
    }

    if (extracted.dependencies && extracted.dependencies.length) {
      html += '<div class="viewer-section">'
        + '<h3 class="viewer-section-title">Dependencies</h3>'
        + '<div class="dep-chips">';
      extracted.dependencies.forEach((d) => {
        html += '<span class="dep-chip">' + escapeHtml(d) + '</span>';
      });
      html += '</div></div>';
    }

    html += buildViewerMeta(result);
    viewerBody.innerHTML = html;
  }

  function buildViewerMeta(result) {
    const parts = [];
    if (result.durationMillis) parts.push((result.durationMillis / 1000).toFixed(1) + 's');
    if (result.agentName) parts.push(escapeHtml(result.agentName));
    if (result.promptTokens != null) {
      parts.push(result.promptTokens + ' → ' + result.completionTokens + ' tok');
    }
    return parts.length
      ? '<div class="viewer-meta">' + parts.join('<span class="dot-sep">·</span>') + '</div>'
      : '';
  }

  // ── Polling ────────────────────────────────────────────────────────────────

  async function refresh() {
    try {
      const [jobRes, filesRes] = await Promise.all([
        fetch(apiUrl('/api/v1/extraction-jobs/' + jobId)),
        fetch(apiUrl('/api/v1/extraction-jobs/' + jobId + '/output-files')),
      ]);
      if (jobRes.ok) renderJob(await jobRes.json());
      if (filesRes.ok) renderFiles(await filesRes.json());
    } catch (e) {
      console.error('Failed to refresh job status', e);
    }
    if (polling) setTimeout(refresh, 2000);
  }

  refresh();
})();
