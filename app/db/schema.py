from __future__ import annotations


SCHEMA_VERSION = 4


INITIAL_SCHEMA_SQL = """
create table if not exists users (
    id integer primary key,
    telegram_user_id integer unique not null,
    username text,
    full_name text,
    role text not null,
    status text not null,
    created_at text not null,
    updated_at text not null,
    approved_by integer,
    rejected_at text,
    revoked_at text,
    source text not null
);

create unique index if not exists idx_users_telegram_user_id
on users(telegram_user_id);

create index if not exists idx_users_status
on users(status);

create index if not exists idx_users_role
on users(role);

create table if not exists access_requests (
    id text primary key,
    telegram_user_id integer not null,
    username text,
    full_name text,
    status text not null,
    created_at text not null,
    resolved_at text,
    resolved_by integer,
    decision_reason text
);

create index if not exists idx_access_requests_user_status
on access_requests(telegram_user_id, status);

create unique index if not exists idx_access_requests_unique_pending_user
on access_requests(telegram_user_id)
where status = 'pending';

create table if not exists documents (
    id text primary key,
    owner_telegram_user_id integer not null,
    document_type text not null default 'receipt',
    status text not null,
    date text,
    time text,
    merchant text,
    amount text,
    currency text not null default 'AMD',
    category text,
    summary_ru text,
    parsed_json text,
    review_payload_json text,
    possible_errors_json text not null default '[]',
    ocr_text_hash text,
    file_stem text,
    parser_version text,
    schema_version text,
    prompt_version text,
    created_at text not null,
    updated_at text not null,
    reviewed_at text,
    deleted_at text
);

create unique index if not exists idx_documents_owner_file_stem
on documents(owner_telegram_user_id, file_stem)
where file_stem is not null;

create index if not exists idx_documents_owner_created
on documents(owner_telegram_user_id, created_at desc);

create index if not exists idx_documents_owner_date
on documents(owner_telegram_user_id, date desc);

create index if not exists idx_documents_status
on documents(status);

create index if not exists idx_documents_type
on documents(document_type);

create index if not exists idx_documents_merchant
on documents(merchant);

create index if not exists idx_documents_category
on documents(category);

create table if not exists document_items (
    id integer primary key,
    document_id text not null references documents(id) on delete cascade,
    position integer not null,
    name_original text,
    name_ru text,
    name_en text,
    unit_price text,
    quantity text,
    unit text,
    line_total text,
    confidence real,
    possible_error text,
    created_at text not null
);

create index if not exists idx_document_items_document
on document_items(document_id, position);

create index if not exists idx_document_items_name_ru
on document_items(name_ru);

create table if not exists document_files (
    id integer primary key,
    document_id text not null references documents(id) on delete cascade,
    kind text not null,
    path text not null,
    mime_type text,
    size_bytes integer,
    sha256 text,
    created_at text not null
);

create index if not exists idx_document_files_document
on document_files(document_id);

create index if not exists idx_document_files_kind
on document_files(kind);

create table if not exists processing_sessions (
    id text primary key,
    telegram_user_id integer not null,
    document_id text references documents(id) on delete set null,
    state text not null,
    document_type text not null,
    session_json text not null,
    created_at text not null,
    updated_at text not null,
    expires_at text
);

create index if not exists idx_processing_sessions_user_state
on processing_sessions(telegram_user_id, state);

create index if not exists idx_processing_sessions_expires
on processing_sessions(expires_at);

create table if not exists usage_events (
    id integer primary key,
    telegram_user_id integer not null,
    event_type text not null,
    document_id text,
    document_type text,
    created_at text not null,
    metadata_json text
);

create index if not exists idx_usage_user_event_created
on usage_events(telegram_user_id, event_type, created_at);

create index if not exists idx_usage_document
on usage_events(document_id);

create table if not exists correction_rules (
    id integer primary key,
    scope text not null,
    source text not null,
    target text not null,
    language text not null default '',
    document_type text not null default '',
    merchant text not null default '',
    usage_count integer not null default 0,
    last_used_at text,
    created_at text not null,
    updated_at text not null,
    owner_telegram_user_id integer not null default 0,
    created_by_telegram_user_id integer
);

create unique index if not exists idx_correction_rules_unique
on correction_rules(owner_telegram_user_id, scope, source, language, document_type, merchant);

create index if not exists idx_correction_rules_scope
on correction_rules(scope);

create index if not exists idx_correction_rules_lookup
on correction_rules(owner_telegram_user_id, scope, source, language, document_type, merchant);

create table if not exists magic_links (
    id text primary key,
    telegram_user_id integer not null,
    token_hash text not null unique,
    purpose text not null,
    created_at text not null,
    expires_at text not null,
    used_at text,
    revoked_at text,
    ip_created text,
    user_agent_used text
);

create unique index if not exists idx_magic_links_token_hash
on magic_links(token_hash);

create index if not exists idx_magic_links_user_expires
on magic_links(telegram_user_id, expires_at);

create table if not exists web_sessions (
    id text primary key,
    telegram_user_id integer not null,
    session_hash text not null unique,
    created_at text not null,
    expires_at text not null,
    revoked_at text,
    last_seen_at text,
    user_agent text,
    ip_address text
);

create unique index if not exists idx_web_sessions_session_hash
on web_sessions(session_hash);

create index if not exists idx_web_sessions_user_expires
on web_sessions(telegram_user_id, expires_at);
"""
