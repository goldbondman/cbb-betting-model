-- Ensure RLS policy for raw.model_features is created idempotently.
alter table raw.model_features enable row level security;

drop policy if exists raw_model_features_read_authenticated on raw.model_features;

create policy raw_model_features_read_authenticated
on raw.model_features
for select
to authenticated
using (true);

-- Optional: keep predictions_latest in sync with the same read-only policy pattern.
alter table raw.predictions_latest enable row level security;

drop policy if exists raw_predictions_latest_read_authenticated on raw.predictions_latest;

create policy raw_predictions_latest_read_authenticated
on raw.predictions_latest
for select
to authenticated
using (true);
