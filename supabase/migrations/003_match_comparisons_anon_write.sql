-- ============================================================
-- 003_match_comparisons_anon_write.sql
-- Allow anon (Oracle server) to upsert match_comparisons.
-- No service role key needed in app code.
-- ============================================================

drop policy if exists "service role manages" on match_comparisons;

create policy "anon upsert"
  on match_comparisons for insert
  to anon with check (true);

create policy "anon update"
  on match_comparisons for update
  to anon using (true);
