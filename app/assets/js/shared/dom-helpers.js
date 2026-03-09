// ══════════════════════════════════════════════════════════
//  DOM HELPERS — reusable UI setup functions
// ══════════════════════════════════════════════════════════

export function setupDrop(zoneId, onDrop) {
  const zone = document.getElementById(zoneId);
  if (!zone) { console.error('setupDrop: element not found:', zoneId); return; }

  zone.addEventListener('dragover', e => { e.preventDefault(); e.stopPropagation(); zone.classList.add('drag-over'); });
  zone.addEventListener('dragleave', e => { e.preventDefault(); if (!zone.contains(e.relatedTarget)) zone.classList.remove('drag-over'); });
  zone.addEventListener('drop', e => {
    e.preventDefault();
    e.stopPropagation();
    zone.classList.remove('drag-over');
    if (e.dataTransfer && e.dataTransfer.files) onDrop(Array.from(e.dataTransfer.files));
  });
}
