// ══════════════════════════════════════════════════════════
//  USERS TAB — admin-only user management
// ══════════════════════════════════════════════════════════
import { state } from '../../shared/state.js';
import { agentBridge } from '../../shared/agent-client.js';

export async function usersLoad() {
  const user = agentBridge.getCurrentUser();
  if (!user || user.role !== 'admin') {
    // Defense in depth — sidebar hides the tab, but a deep-link or stale state could land here
    if (typeof window.switchTool === 'function') window.switchTool('settings');
    return;
  }

  if (!state.agentConnected) {
    const c = document.getElementById('userListV2');
    if (c) c.innerHTML = '<div style="color:#94a3b8; font-size:0.85rem;">Agent offline.</div>';
    return;
  }

  await renderUserList();
}

async function renderUserList() {
  const container = document.getElementById('userListV2');
  if (!container) return;

  const result = await agentBridge.listUsers();
  if (result.error) {
    container.innerHTML = `<div style="color:#dc2626; font-size:0.82rem;">Failed to load users: ${escHtml(result.error)}</div>`;
    return;
  }

  const users = result.users || [];
  const current = agentBridge.getCurrentUser();
  const active = users.filter(u => u.active);
  const inactive = users.filter(u => !u.active);

  const rows = active.map(u => renderUserRow(u, current, false)).join('');
  const inactiveBlock = inactive.length
    ? `<details style="margin-top:14px; font-size:0.82rem; color:#64748b;">
         <summary style="cursor:pointer; padding:8px 0;">Show inactive users (${inactive.length})</summary>
         <div style="margin-top:6px;">${inactive.map(u => renderUserRow(u, current, true)).join('')}</div>
       </details>` : '';

  container.innerHTML = `
    ${rows || '<div style="color:#94a3b8; font-size:0.85rem; padding:14px 0;">No active users.</div>'}
    ${inactiveBlock}
    <div style="font-size:0.7rem; color:#94a3b8; margin-top:14px; padding:10px 12px; background:#fff; border-radius:8px; border:1px dashed #e2e8f0;">
      🔒 Passwords are stored scrambled (bcrypt) and can't be displayed. To help a user who forgot theirs, click <strong>Edit</strong> and set a new one.
    </div>`;
}

function renderUserRow(u, currentUser, isInactive) {
  const isYou = currentUser && currentUser.id === u.id;
  const initials = (u.displayName || u.username || '?')
    .split(/\s+/).map(w => w[0]).slice(0, 2).join('').toUpperCase();
  const roleBadge = u.role === 'admin'
    ? '<span style="background:#fef3c7; color:#92400e; padding:2px 8px; border-radius:4px; font-size:0.7rem; font-weight:600;">ADMIN</span>'
    : '<span style="background:#e0e7ff; color:#3730a3; padding:2px 8px; border-radius:4px; font-size:0.7rem; font-weight:600;">OPERATOR</span>';

  const editBtn = `<button onclick="window.openEditUserModal(${u.id}, '${escAttr(u.username)}', '${escAttr(u.displayName || '')}', '${u.role}', ${u.active})"
        style="background:none; border:1px solid transparent; cursor:pointer; padding:6px; border-radius:6px; color:#64748b;"
        title="Edit user (also resets password)"
        onmouseover="this.style.background='#f1f5f9'; this.style.borderColor='#e2e8f0';"
        onmouseout="this.style.background='none'; this.style.borderColor='transparent';">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
        </svg></button>`;

  let actionBtn;
  if (isInactive) {
    actionBtn = `<button onclick="window.reactivateUser(${u.id})"
        style="background:none; border:1px solid transparent; cursor:pointer; padding:6px; border-radius:6px; color:#16a34a;"
        title="Reactivate user"
        onmouseover="this.style.background='#f0fdf4'; this.style.borderColor='#bbf7d0';"
        onmouseout="this.style.background='none'; this.style.borderColor='transparent';">↻</button>`;
  } else if (isYou) {
    actionBtn = `<button disabled style="background:none; border:1px solid transparent; padding:6px; border-radius:6px; color:#cbd5e1; cursor:not-allowed;"
        title="You can't deactivate yourself">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/>
        </svg></button>`;
  } else {
    actionBtn = `<button onclick="window.confirmDeactivateUser(${u.id}, '${escAttr(u.displayName || u.username)}')"
        style="background:none; border:1px solid transparent; cursor:pointer; padding:6px; border-radius:6px; color:#64748b;"
        title="Deactivate user"
        onmouseover="this.style.color='#dc2626'; this.style.background='#fef2f2'; this.style.borderColor='#fecaca';"
        onmouseout="this.style.color='#64748b'; this.style.background='none'; this.style.borderColor='transparent';">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/>
        </svg></button>`;
  }

  const opacity = isInactive ? '0.6' : '1';
  const statusColor = u.active ? '#16a34a' : '#94a3b8';
  const statusText = u.active ? 'Active' : 'Inactive';

  return `
    <div style="opacity:${opacity}; background:#fff; border:1px solid #e2e8f0; border-radius:10px; padding:12px 14px; display:flex; align-items:center; gap:12px; margin-bottom:8px;">
      <div style="width:36px; height:36px; background:${u.active ? '#f0f9ff' : '#f1f5f9'}; border-radius:9px; display:flex; align-items:center; justify-content:center; font-weight:700; color:${u.active ? '#2563eb' : '#94a3b8'}; font-size:0.85rem;">${escHtml(initials)}</div>
      <div style="flex:1; min-width:0;">
        <div style="font-size:0.88rem; font-weight:600; color:#0f172a;">
          ${escHtml(u.displayName || u.username)}${isYou ? ' <span style="color:#94a3b8; font-weight:400;">(you)</span>' : ''}
        </div>
        <div style="font-size:0.72rem; color:#64748b; margin-top:2px;">@${escHtml(u.username)} · <span style="color:${statusColor};">●</span> ${statusText}</div>
      </div>
      ${roleBadge}
      <div style="display:flex; gap:4px; margin-left:8px;">${editBtn}${actionBtn}</div>
    </div>`;
}

// ── Action handlers exposed on window ──

async function confirmDeactivateUser(id, displayName) {
  const ok = confirm(`Deactivate ${displayName}? They will lose access immediately. You can reactivate them later from "Show inactive users".`);
  if (!ok) return;
  const res = await agentBridge.deleteUser(id);
  if (res.error) {
    alert(`Failed to deactivate user: ${res.error}`);
    return;
  }
  await renderUserList();
}

async function reactivateUser(id) {
  const res = await agentBridge.updateUser(id, { active: true });
  if (res.error) {
    alert(`Failed to reactivate user: ${res.error}`);
    return;
  }
  await renderUserList();
}

// Local escape helpers — re-declared so this module is self-contained.
// (`escHtml` also lives in shared/utils.js but we don't import it to keep
// this module loadable in isolation.)
function escHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
}
function escAttr(s) {
  return String(s ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

// Expose action handlers on window so inline onclick="" attributes can call them.
// (Task 11 will move openAddUserModal / openEditUserModal / saveUser here from
// settings.js — for now they're still in settings.js, so renderUserRow's edit
// button references window.openEditUserModal which is set up by settings.js.)
window.confirmDeactivateUser = confirmDeactivateUser;
window.reactivateUser = reactivateUser;
