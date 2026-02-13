-- Purpose:
-- raw.model_features received new rolling-window diff columns in ml/model_features.csv.
-- Add the missing nullable numeric columns so load_csv_to_db.py can load nightly CSVs without schema drift failures.

alter table raw.model_features add column if not exists ortg_l3_pre_diff double precision;
alter table raw.model_features add column if not exists drtg_l3_pre_diff double precision;
alter table raw.model_features add column if not exists netrtg_l3_pre_diff double precision;
alter table raw.model_features add column if not exists pace_l3_pre_diff double precision;
alter table raw.model_features add column if not exists efg_l3_pre_diff double precision;
alter table raw.model_features add column if not exists tov_pct_l3_pre_diff double precision;
alter table raw.model_features add column if not exists orb_pct_l3_pre_diff double precision;
alter table raw.model_features add column if not exists drb_pct_l3_pre_diff double precision;
alter table raw.model_features add column if not exists ftr_l3_pre_diff double precision;
alter table raw.model_features add column if not exists "3par_l3_pre_diff" double precision;
