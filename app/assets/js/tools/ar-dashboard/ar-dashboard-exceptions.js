// AR Dashboard — exception detection
// Inspects the loaded model + returns a flat list of exceptions.
//
// Each exception has:
//   id           stable string identifier
//   category     'suspense' | 'short' | 'over' | 'posting_gap' | 'amount_disagreement'
//                | 'customer_mismatch' | 'missing_tms' | 'non_factored'
//   severity     'urgent' | 'warning' | 'info'
//   invoice      invoice # (if applicable)
//   customer     customer name + id
//   details      object — category-specific data shown in worklist row
//   suggested_action  short string
//
// Categories 9 (TAB BANK posting error) + 10 (UC reclassification) from the
// 2026-06-01 spec update are not yet implemented — they need Jihyun's
// confirmation on detection rules + UX. Wire in once she responds.

export function arDetectExceptions(model) {
  const out = [];
  out.push(...detectShortPays(model));
  out.push(...detectOverPays(model));
  out.push(...detectAmountDisagreements(model));
  out.push(...detectMissingTms(model));
  // Suspense / customer-mismatch / non-factored / posting gaps require the raw
  // TAB BANK file (loaded separately via Phase I source-files drop).
  if (model.tab_bank) {
    out.push(...detectSuspense(model));
    out.push(...detectCustomerMismatches(model));
    out.push(...detectNonFactored(model));
    out.push(...detectPostingGaps(model));
  }
  return out;
}

function detectShortPays(model) {
  return model.ar_register
    .filter(r => (r.paid ?? 0) > 0 && (r.balance ?? 0) > 0)
    .map(r => ({
      id: `short:${r.inv}`,
      category: 'short',
      severity: 'warning',
      invoice: r.inv,
      customer: { id: r.new_id, name: r.company },
      details: {
        amount: r.amount,
        paid: r.paid,
        balance: r.balance,
        memo: r.memo,
      },
      suggested_action: 'Call customer or write off',
    }));
}

function detectOverPays(model) {
  return model.ar_register
    .filter(r => (r.balance ?? 0) < -0.01)
    .map(r => ({
      id: `over:${r.inv}`,
      category: 'over',
      severity: 'warning',
      invoice: r.inv,
      customer: { id: r.new_id, name: r.company },
      details: { amount: r.amount, paid: r.paid, balance: r.balance },
      suggested_action: 'Open Overpayment Workflow',
    }));
}

function detectAmountDisagreements(model) {
  // ADJUSTMENT sheet IS the authoritative list of amount disagreements
  // (these were already flagged + applied by the build engine).
  return model.adjustments.map(adj => ({
    id: `amount:${adj.inv_no}`,
    category: 'amount_disagreement',
    severity: 'info',
    invoice: adj.inv_no,
    customer: { id: adj.id, name: adj.name },
    details: {
      delta: adj.amount_difference,
      revised: adj.revised_invoice_amount,
      paid_received: adj.paid_received,
      wo_no: adj.wo_no,
    },
    suggested_action: 'Confirm TMS revision applied correctly',
  }));
}

function detectMissingTms(model) {
  // Invoices in QBO Schedule but not in TMS Reconcile.
  const tmsInvs = new Set(model.tms_rows.map(t => t.inv_no).filter(Boolean));
  return model.schedule
    .filter(s => s.inv && !tmsInvs.has(s.inv))
    .map(s => ({
      id: `notms:${s.inv}`,
      category: 'missing_tms',
      severity: 'info',
      invoice: s.inv,
      customer: { id: s.customer_id, name: s.customer_name },
      details: { amount: s.amount, cntr_chassis: s.cntr_chassis, ref: s.ref },
      suggested_action: 'Confirm warehouse / manual entry',
    }));
}

function detectSuspense(model) {
  if (!model.tab_bank) return [];
  return model.tab_bank
    .filter(r => r.pmt_type === 'Unapplied Cash' && r.desc !== 'NON-FACTORED')
    .map(r => ({
      id: `suspense:${r.check}:${r.invoice}`,
      category: 'suspense',
      severity: 'urgent',
      invoice: r.invoice,
      customer: { id: r.debtor_code, name: r.debtor_name },
      details: {
        check: r.check,
        amount: r.collected_amount,
      },
      suggested_action: 'Match to customer + apply',
    }));
}

function detectCustomerMismatches(model) {
  if (!model.tab_bank) return [];
  const byCheck = new Map();
  for (const c of model.collections) {
    if (c.check_no) {
      const list = byCheck.get(c.check_no) || [];
      list.push(c);
      byCheck.set(c.check_no, list);
    }
  }
  const out = [];
  for (const t of model.tab_bank) {
    if (t.pmt_type !== 'Payment') continue;
    const qboHits = byCheck.get(t.check) || [];
    if (qboHits.length === 0) continue;
    const qboCust = qboHits[0].customer_name || '';
    if (!fuzzyNameMatch(t.debtor_name, qboCust)) {
      out.push({
        id: `cmismatch:${t.check}`,
        category: 'customer_mismatch',
        severity: 'warning',
        invoice: null,
        customer: { id: t.debtor_code, name: t.debtor_name },
        details: {
          check: t.check,
          tab_bank_name: t.debtor_name,
          qbo_name: qboCust,
        },
        suggested_action: 'Confirm correct customer',
      });
    }
  }
  return out;
}

function detectNonFactored(model) {
  if (!model.tab_bank) return [];
  return model.tab_bank
    .filter(r => r.desc === 'NON-FACTORED' && r.pmt_type === 'Unapplied Cash')
    .map(r => ({
      id: `nonfact:${r.check}`,
      category: 'non_factored',
      severity: 'info',
      invoice: null,
      customer: { id: null, name: '(suspense)' },
      details: { check: r.check, amount: r.collected_amount },
      suggested_action: 'No action — informational',
    }));
}

function detectPostingGaps(model) {
  if (!model.tab_bank) return [];
  const qboChecks = new Set(model.collections.map(c => c.check_no).filter(Boolean));
  const seen = new Set();
  const out = [];
  for (const t of model.tab_bank) {
    if (t.pmt_type !== 'Payment') continue;
    if (qboChecks.has(t.check)) continue;
    if (seen.has(t.check)) continue;
    seen.add(t.check);
    out.push({
      id: `gap:${t.check}`,
      category: 'posting_gap',
      severity: 'urgent',
      invoice: null,
      customer: { id: t.debtor_code, name: t.debtor_name },
      details: { check: t.check, amount: t.amount },
      suggested_action: 'Post in QBO',
    });
  }
  return out;
}

function fuzzyNameMatch(a, b) {
  if (!a || !b) return false;
  const norm = s => String(s).toLowerCase().replace(/[^a-z0-9]/g, '');
  const na = norm(a), nb = norm(b);
  if (na === nb) return true;
  const len = Math.min(na.length, nb.length);
  const longer = Math.max(na.length, nb.length);
  if (len / longer < 0.5) return false;
  return na.startsWith(nb) || nb.startsWith(na);
}

window.arDetectExceptions = arDetectExceptions;
window.arFuzzyNameMatch = fuzzyNameMatch;
