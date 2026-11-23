-- 공지왔수뭉 마감 알림 메일용 데이터베이스 (Supabase 무료 플랜으로 충분)
-- Supabase 대시보드 → SQL Editor 에 통째로 붙여넣고 Run 하면 됩니다.
--
-- 원칙
-- 1) 사이트(브라우저)는 아래 함수(RPC)로만 쓰기 가능하고, 다른 사람의 데이터는 읽을 수 없습니다.
-- 2) 처음 등록하면 확인 메일을 보내고, 링크를 눌러 확인한 주소에만 알림을 보냅니다(남의 메일 도용 방지).

create table if not exists subscriptions (
  token            uuid primary key,                 -- 브라우저가 만든 관리용 키 (구독 수정·해지에 사용)
  email            text not null check (email ~* '^[^@\s]+@[^@\s]+\.[^@\s]+$' and length(email) <= 200),
  leads            int[] not null default '{1}',     -- 며칠 전에 알릴지: 0=당일, 1, 3, 7
  want_calendar    boolean not null default true,    -- 내 캘린더에 추가한 일정
  want_notices     boolean not null default true,    -- 관심 카테고리·키워드 공지 마감
  campus           text not null default '천안',
  categories       text[] not null default '{}',
  keywords         text[] not null default '{}',
  items            jsonb not null default '[]' check (jsonb_typeof(items) = 'array' and jsonb_array_length(items) <= 500),
  confirmed        boolean not null default false,
  confirm_sent_at  timestamptz,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

create table if not exists sent_log (
  token     uuid references subscriptions(token) on delete cascade,
  item_key  text not null,
  lead      int  not null,
  sent_on   date not null default (now() at time zone 'Asia/Seoul')::date,
  primary key (token, item_key, lead)
);

-- 브라우저(anon)는 테이블을 직접 읽거나 쓸 수 없음. 정책을 만들지 않으면 전부 막힙니다.
alter table subscriptions enable row level security;
alter table sent_log      enable row level security;

-- 구독 등록·수정 ---------------------------------------------------------------
create or replace function upsert_subscription(
  p_token uuid, p_email text, p_leads int[],
  p_want_calendar boolean, p_want_notices boolean,
  p_campus text, p_categories text[], p_keywords text[], p_items jsonb
) returns text
language plpgsql security definer set search_path = public as $$
declare st text;
begin
  if p_leads is null or not (p_leads <@ array[0,1,3,7]) then
    raise exception 'leads must be subset of {0,1,3,7}';
  end if;
  insert into subscriptions as s (token, email, leads, want_calendar, want_notices,
                                  campus, categories, keywords, items)
  values (p_token, lower(trim(p_email)), p_leads, p_want_calendar, p_want_notices,
          coalesce(p_campus, '천안'), coalesce(p_categories, '{}'), coalesce(p_keywords, '{}'),
          coalesce(p_items, '[]'))
  on conflict (token) do update set
    email = excluded.email, leads = excluded.leads,
    want_calendar = excluded.want_calendar, want_notices = excluded.want_notices,
    campus = excluded.campus, categories = excluded.categories, keywords = excluded.keywords,
    items = excluded.items,
    -- 메일 주소를 바꾸면 다시 확인받기
    confirmed = case when s.email = excluded.email then s.confirmed else false end,
    confirm_sent_at = case when s.email = excluded.email then s.confirm_sent_at else null end,
    updated_at = now();
  select case when confirmed then 'confirmed' else 'pending' end into st from subscriptions where token = p_token;
  return st;
end $$;

-- 내 캘린더가 바뀔 때마다 일정 목록만 갱신
create or replace function sync_items(p_token uuid, p_items jsonb) returns void
language sql security definer set search_path = public as $$
  update subscriptions set items = coalesce(p_items, '[]'), updated_at = now() where token = p_token;
$$;

create or replace function confirm_subscription(p_token uuid) returns boolean
language sql security definer set search_path = public as $$
  update subscriptions set confirmed = true, updated_at = now() where token = p_token returning true;
$$;

create or replace function unsubscribe(p_token uuid) returns boolean
language sql security definer set search_path = public as $$
  delete from subscriptions where token = p_token returning true;
$$;

revoke all on function upsert_subscription, sync_items, confirm_subscription, unsubscribe from public;
grant execute on function upsert_subscription, sync_items, confirm_subscription, unsubscribe to anon;
