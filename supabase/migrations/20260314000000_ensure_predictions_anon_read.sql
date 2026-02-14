-- Ensure predictions tables have proper RLS policies for anonymous reads
-- This migration reinforces the policies to ensure Streamlit (using anon key) can read predictions

-- Ensure public.predictions allows anonymous reads
alter table if exists public.predictions enable row level security;

drop policy if exists predictions_read on public.predictions;
create policy predictions_read on public.predictions
for select to anon, authenticated
using (true);

-- Ensure raw.predictions_latest allows anonymous reads (if table exists)
do $$
begin
  if exists (
    select 1 from information_schema.tables 
    where table_schema = 'raw' 
    and table_name = 'predictions_latest'
  ) then
    alter table raw.predictions_latest enable row level security;
    
    drop policy if exists raw_predictions_latest_read_anon on raw.predictions_latest;
    create policy raw_predictions_latest_read_anon
    on raw.predictions_latest
    for select to anon, authenticated
    using (true);
  end if;
end
$$;

-- Add helpful comment
comment on policy predictions_read on public.predictions is 
  'Allow anonymous and authenticated users to read predictions (for Streamlit app)';
