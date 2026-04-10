/**
 * SRE Control Center Logic
 * Handles real-time polling, metrics updates, and incident actions.
 */

// ─── DOM Elements ──────────────────────────────────────────────────
const DOM = {
    agents: document.getElementById('metric-agents'),
    policy: document.getElementById('metric-policy'),
    resolved: document.getElementById('metric-resolved'),
    incidentTbody: document.getElementById('incident-tbody'),
    approvalsContainer: document.getElementById('approvals-container'),
    btnDemo: document.getElementById('trigger-demo'),
};

// ─── API Endpoints ─────────────────────────────────────────────────
const API = {
    status: '/api/v1/status',
    incidents: '/api/v1/incidents?limit=25',
    process: '/api/v1/events/process',
    approve: (id) => `/api/v1/incidents/${id}/approve`,
};

// ─── State ─────────────────────────────────────────────────────────
let knownIncidents = new Set();
let pendingApprovals = new Set();

// ─── Initialization ────────────────────────────────────────────────
async function init() {
    DOM.btnDemo.addEventListener('click', triggerSyntheticAnomaly);
    
    // Initial fetch
    await fetchStatus();
    await fetchIncidents();

    // Polling loop
    setInterval(fetchStatus, 3000);
    setInterval(fetchIncidents, 2000);
}

// ─── Polling Functions ─────────────────────────────────────────────
async function fetchStatus() {
    try {
        const res = await fetch(API.status);
        if (!res.ok) return;
        const data = await res.json();
        
        // Update top metrics
        DOM.resolved.innerText = data.resolved_incidents;
        if (data.feedback_policy) {
            DOM.policy.innerText = data.feedback_policy.current_version || 'v1.0';
        }
    } catch (err) {
        console.error('Failed to fetch status:', err);
    }
}

async function fetchIncidents() {
    try {
        const res = await fetch(API.incidents);
        if (!res.ok) return;
        const data = await res.json();
        
        renderIncidentsTable(data.incidents);
        renderApprovalsQueue(data.incidents);
        
    } catch (err) {
        console.error('Failed to fetch incidents:', err);
    }
}

// ─── Rendering ─────────────────────────────────────────────────────
function renderIncidentsTable(incidents) {
    if (!incidents || incidents.length === 0) return;
    
    // Reverse to show newest first
    const sorted = [...incidents].reverse();
    
    DOM.incidentTbody.innerHTML = '';
    
    sorted.forEach(inc => {
        const tr = document.createElement('tr');
        
        const severityClass = inc.anomaly_event?.severity || 'medium';
        const statusClass = inc.status || 'unknown';
        
        let actionStr = 'Analyzing...';
        if (inc.action_results && inc.action_results.length > 0) {
            actionStr = inc.action_results[0].action_taken;
        }

        let rcStr = 'Investigating...';
        if (inc.diagnosis_result) {
            rcStr = inc.diagnosis_result.root_cause_category;
        }

        let expertStr = '-';
        if (inc.diagnosis_result && inc.diagnosis_result.sub_agent_reports) {
            expertStr = Object.keys(inc.diagnosis_result.sub_agent_reports)
                .map(exp => `<span class="badge expert-${exp}">${exp}</span>`)
                .join(' ');
        }

        tr.innerHTML = `
            <td style="font-family: monospace;">${inc.incident_id.substring(0, 8)}</td>
            <td><span class="badge ${severityClass.toLowerCase()}">${severityClass}</span></td>
            <td>${inc.anomaly_event?.anomaly_type || '-'}</td>
            <td>${rcStr}</td>
            <td>${expertStr}</td>
            <td>${actionStr}</td>
            <td><span class="badge ${statusClass.toLowerCase()}">${statusClass.replace('_', ' ')}</span></td>
            <td>$${(inc.total_llm_cost_usd || 0).toFixed(4)}</td>
        `;
        
        DOM.incidentTbody.appendChild(tr);
    });
}

function renderApprovalsQueue(incidents) {
    if (!incidents) return;
    
    // Filter for action_pending
    const pending = incidents.filter(i => i.status === 'ACTION_PENDING');
    
    if (pending.length === 0) {
        DOM.approvalsContainer.innerHTML = `<div class="empty-state">No pending actions. System is stable.</div>`;
        pendingApprovals.clear();
        return;
    }
    
    // Only re-render if the set of pending IDs has changed to avoid flickering buttons
    const currentPendingIds = new Set(pending.map(i => i.incident_id));
    let changed = false;
    for (let id of currentPendingIds) {
        if (!pendingApprovals.has(id)) changed = true;
    }
    for (let id of pendingApprovals) {
        if (!currentPendingIds.has(id)) changed = true;
    }
    
    if (!changed) return; 
    pendingApprovals = currentPendingIds;
    
    DOM.approvalsContainer.innerHTML = '';
    
    pending.forEach(inc => {
        if (!inc.action_results || inc.action_results.length === 0) return;
        const action = inc.action_results[0];
        
        const item = document.createElement('div');
        item.className = 'approval-item';
        item.innerHTML = `
            <h4>Action Required: ${action.action_taken}</h4>
            <p><strong>Incident:</strong> ${inc.incident_id}</p>
            <p><strong>Reason:</strong> ${inc.diagnosis_result?.reasoning_chain.substring(0, 100)}...</p>
            <div class="code-block">${JSON.stringify(action.output?.recommended_params || {})}</div>
            
            <div class="approval-actions">
                <button class="btn success" onclick="handleApprove('${inc.incident_id}', true)">Approve Execution</button>
                <button class="btn danger" onclick="handleApprove('${inc.incident_id}', false)">Reject Override</button>
            </div>
        `;
        DOM.approvalsContainer.appendChild(item);
    });
}

// ─── Actions ───────────────────────────────────────────────────────
async function triggerSyntheticAnomaly() {
    DOM.btnDemo.disabled = true;
    DOM.btnDemo.innerText = 'Injecting...';
    
    try {
        const payload = {
            source: "payment_gateway",
            service_name: "payment-gateway",
            event_type: "metric",
            payload: {
                timestamp: new Date().toISOString(),
                cpu_percent: 85.5,
                memory_percent: 92.1,
                p99_latency_ms: 1500.0, // Spike!
                error_rate: 0.15 // Spike!
            }
        };
        
        await fetch(API.process, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        
        // Force immediate fetch
        setTimeout(fetchIncidents, 500);
        
    } catch (err) {
        console.error('Demo trigger failed:', err);
    } finally {
        setTimeout(() => {
            DOM.btnDemo.disabled = false;
            DOM.btnDemo.innerText = 'Synthesize Telemetry Event';
        }, 1000);
    }
}

async function handleApprove(incidentId, approved) {
    try {
        const buttons = document.querySelectorAll(`button[onclick*="${incidentId}"]`);
        buttons.forEach(b => b.disabled = true);
        
        await fetch(API.approve(incidentId), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ approved: approved })
        });
        
        // Immediate refresh to show status change
        fetchIncidents();
        
    } catch (err) {
        console.error('Approval failed:', err);
        alert('Failed to process approval.');
    }
}

// Start
document.addEventListener('DOMContentLoaded', init);
