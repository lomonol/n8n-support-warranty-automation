-- Sequential ticket numbers (TCK-2026-000001, ...). Run once on the Supabase project.
-- The intake workflow no longer sets ticket_number; this default fills it.
create sequence if not exists public.support_ticket_seq start 1 minvalue 1;

alter table public.support_tickets
  alter column ticket_number set default (
    'TCK-' || to_char(now(), 'YYYY') || '-' || lpad(nextval('public.support_ticket_seq')::text, 6, '0')
  );
