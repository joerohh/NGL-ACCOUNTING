'use strict';
// ══════════════════════════════════════════════════════════
//  MERGE WORKER — Parallel PDF merging in a Web Worker
//  Loads pdf-lib via importScripts, receives ArrayBuffers,
//  returns merged PDF bytes via Transferable API (zero-copy)
// ══════════════════════════════════════════════════════════

importScripts('https://unpkg.com/pdf-lib@1.17.1/dist/pdf-lib.min.js');

self.onmessage = async function (e) {
  const { taskId, containerNumber, pdfBuffers, pdfNames, filename, subfolder } = e.data;

  try {
    const { PDFDocument } = PDFLib;
    const merged = await PDFDocument.create();

    for (let i = 0; i < pdfBuffers.length; i++) {
      try {
        const doc = await PDFDocument.load(pdfBuffers[i], {
          ignoreEncryption: true,
          updateMetadata: false,
        });
        const pages = await merged.copyPages(doc, doc.getPageIndices());
        pages.forEach(p => merged.addPage(p));
      } catch (err) {
        self.postMessage({
          type: 'error',
          taskId,
          containerNumber,
          error: '"' + pdfNames[i] + '": ' + err.message,
        });
        return;
      }
    }

    const bytes = await merged.save({
      objectsPerTick: Infinity,
      updateFieldAppearances: false,
      addDefaultPage: false,
    });

    // Transfer the buffer back (zero-copy, no serialization overhead)
    self.postMessage(
      { type: 'result', taskId, containerNumber, mergedBytes: bytes, filename, subfolder },
      [bytes.buffer]
    );

  } catch (err) {
    self.postMessage({
      type: 'error',
      taskId,
      containerNumber,
      error: err.message,
    });
  }
};
