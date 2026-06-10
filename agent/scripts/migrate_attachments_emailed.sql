-- One-time migration: rename audit_log.attachments_found → attachments_emailed
-- Run once against local SQLite (~/AppData/Local/NGL Accounting/data/ngl.db)
-- AND once against Supabase (xkiunwaobjhpjhtzpvvs.supabase.co) via SQL editor.
--
-- Both SQLite and Postgres support ALTER TABLE RENAME COLUMN syntax.
ALTER TABLE audit_log RENAME COLUMN attachments_found TO attachments_emailed;
