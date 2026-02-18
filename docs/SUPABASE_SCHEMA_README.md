# Supabase Schema Documentation Index

This directory contains comprehensive documentation for the CBB Betting Model's Supabase database schema.

## 📄 Documents

### 1. [SUPABASE_SCHEMA_DESIGN.md](./SUPABASE_SCHEMA_DESIGN.md) - Complete Design Specification
**79KB | Comprehensive Design Document**

The authoritative, complete schema design for the CBB Betting Model Supabase database.

**Contents:**
- Complete table definitions with all columns, types, and constraints
- Row Level Security (RLS) policies for all tables
- Database rules, defaults, and check constraints  
- Edge functions and triggers with full implementations
- Index strategy and performance optimization
- Detailed design rationale for every decision

**Use this document when:**
- Understanding the full database architecture
- Learning why specific design choices were made
- Implementing new features that interact with the database
- Reviewing the schema for optimization opportunities

---

### 2. [SUPABASE_SCHEMA_IMPLEMENTATION_GUIDE.md](./SUPABASE_SCHEMA_IMPLEMENTATION_GUIDE.md) - Implementation Instructions
**11KB | Step-by-Step Guide**

Practical guide for implementing the schema in your Supabase project.

**Contents:**
- Pre-migration checklist and backup procedures
- Step-by-step migration instructions (CLI, Dashboard, psql)
- Verification queries to confirm successful migration
- Data population and backfill strategies
- Application code update examples
- Rollback procedures if issues arise

**Use this document when:**
- Running the schema migration for the first time
- Setting up a new environment (dev, staging, production)
- Troubleshooting migration issues
- Planning a rollback strategy

---

### 3. [SUPABASE_SCHEMA_QUICK_REFERENCE.md](./SUPABASE_SCHEMA_QUICK_REFERENCE.md) - Quick Reference Guide
**13KB | Day-to-Day Reference**

Quick reference for daily development work with the database.

**Contents:**
- Table overview with key columns summary
- Entity relationship diagrams (ASCII art)
- Common query patterns with examples
- Index reference for optimization
- Data types and standards reference
- Best practices DO/DON'T list
- Troubleshooting tips

**Use this document when:**
- Writing queries against the database
- Looking up table names or column names
- Finding example queries for common tasks
- Optimizing slow queries
- Debugging data issues

---

## 🗂️ Schema Organization

The database uses a **three-schema architecture**:

```
┌────────────────────────────────────────┐
│ raw schema                             │
│ • Immutable source data                │
│ • Full API payloads (JSONB)            │
│ • Multi-source tracking                │
│ • 13 tables                            │
└────────────────────────────────────────┘
                  ↓
┌────────────────────────────────────────┐
│ public schema                          │
│ • Normalized application data          │
│ • Foreign key relationships            │
│ • Production-ready                     │
│ • 13 tables                            │
└────────────────────────────────────────┘
                  ↓
┌────────────────────────────────────────┐
│ analytics schema                       │
│ • Computed metrics                     │
│ • Rolling windows                      │
│ • Performance tracking                 │
│ • 7 tables                             │
└────────────────────────────────────────┘
```

### Schema Purpose

**Raw Schema** (`raw.*`)
- Store complete, unmodified data from external sources
- Enable retroactive data extraction (JSONB payloads)
- Track data quality and multi-source conflicts
- Examples: `raw.raw_games`, `raw.espn_team_game_core`, `raw.barttorvik_teams`

**Public Schema** (`public.*`)
- Normalized, cleaned data ready for application use
- Enforced foreign key relationships
- Direct integration with Streamlit app
- Examples: `public.teams`, `public.games`, `public.predictions`

**Analytics Schema** (`analytics.*`)
- Derived metrics computed from raw data
- Can be regenerated if formulas change
- Optimized for analytical queries
- Examples: `analytics.team_game_metrics`, `analytics.team_rolling_metrics`

---

## 🚀 Quick Start

### For First-Time Setup

1. **Read**: [SUPABASE_SCHEMA_DESIGN.md](./SUPABASE_SCHEMA_DESIGN.md) - Understand the architecture
2. **Execute**: [SUPABASE_SCHEMA_IMPLEMENTATION_GUIDE.md](./SUPABASE_SCHEMA_IMPLEMENTATION_GUIDE.md) - Run the migration
3. **Reference**: [SUPABASE_SCHEMA_QUICK_REFERENCE.md](./SUPABASE_SCHEMA_QUICK_REFERENCE.md) - Keep handy for daily work

### For Daily Development

Start with: [SUPABASE_SCHEMA_QUICK_REFERENCE.md](./SUPABASE_SCHEMA_QUICK_REFERENCE.md)

### For Schema Changes

1. Consult: [SUPABASE_SCHEMA_DESIGN.md](./SUPABASE_SCHEMA_DESIGN.md) - Understand design principles
2. Plan migration following patterns in: `supabase/migrations/20260318000000_complete_schema_design.sql`
3. Update all three documentation files to reflect changes

---

## 📊 Key Statistics

- **Total Tables**: 33 (13 raw + 13 public + 7 analytics)
- **Total Indexes**: ~100 (including partial and GIN indexes)
- **Foreign Key Relationships**: ~30
- **RLS Policies**: 33 (one per table)
- **Triggers**: 8 (auto-update timestamps)
- **Custom Functions**: 2 (utility functions)

---

## 🔑 Key Design Principles

### 1. Aggressive Normalization
**Raw data ≠ Derived calculations**

❌ **Bad**: Box score stats + efficiency metrics in same table
```sql
-- DON'T DO THIS
CREATE TABLE team_stats (
  fgm INTEGER,           -- Raw
  fga INTEGER,           -- Raw
  efg_pct NUMERIC,       -- Derived!
  ortg NUMERIC           -- Derived!
);
```

✅ **Good**: Separate tables for raw vs derived
```sql
-- Raw data
CREATE TABLE public.team_boxscores (
  fgm INTEGER,
  fga INTEGER
);

-- Derived metrics (separate table)
CREATE TABLE analytics.team_game_metrics (
  boxscore_id UUID REFERENCES public.team_boxscores(id),
  efg_pct NUMERIC,
  ortg NUMERIC
);
```

### 2. Multi-Source Integrity
**Track data provenance and conflicts**

Every ingested table has:
- `source` column: Which API provided this data?
- `verification_status`: Has it been cross-checked?
- `verification_notes`: Human-readable conflict resolution

### 3. Query Performance
**Strategic indexing for real-world usage patterns**

- Partial indexes on filtered queries (e.g., `WHERE status != 'final'`)
- Composite indexes for common multi-column queries
- GIN indexes for JSONB column queries
- Expression indexes for computed filters

### 4. Audit Trails
**Never lose data; track everything**

All tables include:
- `created_at`: When was this row created?
- `updated_at`: When was it last modified?
- `pulled_at`: When was data fetched from source API?

### 5. Schema Evolution
**JSONB for flexible, future-proof fields**

Use JSONB for:
- Model parameters (may change frequently)
- Feature vectors (may add/remove features)
- External API responses (schema outside our control)
- Segment breakdowns (flexible slicing)

---

## 🛠️ Common Tasks

### View All Tables
```sql
SELECT schemaname, tablename 
FROM pg_tables 
WHERE schemaname IN ('raw', 'public', 'analytics')
ORDER BY schemaname, tablename;
```

### Check Table Sizes
```sql
SELECT 
  schemaname,
  tablename,
  pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables
WHERE schemaname IN ('raw', 'public', 'analytics')
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;
```

### View Foreign Key Relationships
```sql
SELECT
  tc.table_schema, 
  tc.table_name, 
  kcu.column_name,
  ccu.table_schema AS foreign_table_schema,
  ccu.table_name AS foreign_table_name,
  ccu.column_name AS foreign_column_name
FROM information_schema.table_constraints AS tc 
JOIN information_schema.key_column_usage AS kcu
  ON tc.constraint_name = kcu.constraint_name
  AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage AS ccu
  ON ccu.constraint_name = tc.constraint_name
  AND ccu.table_schema = tc.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
  AND tc.table_schema IN ('raw', 'public', 'analytics')
ORDER BY tc.table_schema, tc.table_name;
```

### Check RLS Status
```sql
SELECT 
  schemaname, 
  tablename, 
  rowsecurity AS rls_enabled
FROM pg_tables 
WHERE schemaname IN ('raw', 'public', 'analytics')
ORDER BY schemaname, tablename;
```

---

## 📖 Additional Resources

### In This Repository
- **Migration File**: `../supabase/migrations/20260318000000_complete_schema_design.sql`
- **Data Flow Docs**: `DATA_FLOW.md` - How data moves through the system
- **Multi-Source Docs**: `MULTI_SOURCE_INTEGRATION.md` - Multi-source data handling

### External References
- [Supabase Documentation](https://supabase.com/docs)
- [PostgreSQL Documentation](https://www.postgresql.org/docs/)
- [Row Level Security Guide](https://supabase.com/docs/guides/auth/row-level-security)

---

## 🤝 Contributing

When making schema changes:

1. **Update migration file** in `supabase/migrations/`
2. **Update all three documentation files**:
   - Full design in `SUPABASE_SCHEMA_DESIGN.md`
   - Implementation notes in `SUPABASE_SCHEMA_IMPLEMENTATION_GUIDE.md`
   - Quick reference in `SUPABASE_SCHEMA_QUICK_REFERENCE.md`
3. **Test migration** on development database
4. **Update application code** to use new schema
5. **Document breaking changes** in PR description

---

## ⚠️ Important Notes

### Migration Safety
- The migration is **additive and non-destructive**
- Existing tables are enhanced, not dropped
- Always backup before running migrations
- Test on development environment first

### Performance Considerations
- Run `ANALYZE` on tables after bulk inserts
- Monitor slow queries with `pg_stat_statements`
- Review index usage with `pg_stat_user_indexes`
- Consider partitioning if tables exceed 100M rows

### Security
- RLS policies enforce read/write access
- `anon` role: Read-only access to public-facing data
- `authenticated` role: Read all, write to `bet_ledger`
- `service_role`: Full access (use only in backend)

---

## 🆘 Getting Help

1. **Check Quick Reference**: Most common tasks are documented
2. **Review Design Doc**: Understand the "why" behind decisions
3. **Consult Implementation Guide**: Step-by-step troubleshooting
4. **Open an Issue**: If documentation is unclear or incorrect

---

## 📜 Version History

| Version | Date | Description |
|---------|------|-------------|
| 1.0 | 2026-02-18 | Initial comprehensive schema design |

---

## 📝 Summary

This schema represents a **production-ready, scalable, maintainable** database design for the CBB Betting Model. It follows database design best practices while being optimized for the specific needs of a sports betting prediction system.

**Key Features:**
✅ Aggressive normalization (raw ≠ derived)
✅ Multi-source data integrity
✅ Query performance optimization
✅ Complete audit trails
✅ Schema evolution support
✅ Comprehensive documentation

**Ready to implement?** Start with the [Implementation Guide](./SUPABASE_SCHEMA_IMPLEMENTATION_GUIDE.md)!
